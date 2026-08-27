import { statSync } from "node:fs";

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import {
  StickerAutoPolicyStore,
  StickerSelectionStore,
} from "./sticker-selection.ts";
import { StickerTurnStore } from "./sticker-turn.ts";
import {
  assistantTextFromEvent,
  compactDecisionLog,
  parseStickerBridgeDecision,
} from "./sticker-v21-utils.ts";
import {
  channelFromContext,
  channelIsAllowed,
  contextAliases,
  isNativeVoiceContext,
  messageExplicitlyRequestsSticker,
  messageForbidsSticker,
  optionalString,
  parseStickerCandidates,
  preferredPolicyKey,
  readConfig,
  readStickerDeliveryConfig,
  resolveStickerFile,
  resolveUserId,
  runBridge,
  senderIdFromContext,
  sessionKeyFromContext,
  stripStickerCandidateContext,
} from "./sticker-plugin-utils.ts";

const PLUGIN_ID = "yogurt-social-injector";
const WEIXIN_CHANNEL = "openclaw-weixin";
const STICKER_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$/;
const AGENT_END_DELIVERY_DELAY_MS = (() => {
  const value = Number.parseInt(
    process.env.YOGURT_STICKER_AGENT_END_DELAY_MS || "",
    10,
  );
  return Number.isFinite(value) ? Math.min(10_000, Math.max(0, value)) : 2500;
})();

const selectionStore = new StickerSelectionStore();
const autoPolicyStore = new StickerAutoPolicyStore();
const turnStore = new StickerTurnStore();
const deliveryTimers = new Map<string, NodeJS.Timeout>();

function addPeerAlias(aliases: string[], channel: string, peerId: unknown): string[] {
  const resolvedPeerId = optionalString(peerId);
  if (!resolvedPeerId) return aliases;
  return [...new Set([...aliases, `peer:${channel}:${resolvedPeerId}`])];
}

function accountIdFromContext(ctx: any): string | undefined {
  return (
    optionalString(ctx?.accountId) ||
    optionalString(ctx?.channelContext?.accountId) ||
    optionalString(ctx?.requester?.accountId) ||
    optionalString(ctx?.deliveryContext?.accountId)
  );
}

function targetFromContext(ctx: any, event?: any): string | undefined {
  return (
    optionalString(event?.to) ||
    optionalString(ctx?.chatId) ||
    optionalString(ctx?.conversationId) ||
    optionalString(ctx?.channelContext?.chat?.id) ||
    senderIdFromContext(ctx)
  );
}

function buildExplicitStickerAvailableContext(): string {
  return [
    "[Yogurt Sticker Delivery]",
    "用户明确要求发送表情包，系统会在本轮文字回复之后根据最终语义选择一张安全图片。",
    "正文只需要一句很短、自然的确认，建议不超过 20 个汉字。",
    "不要描述、解释或虚构表情包画面，也不要提及 ID、文件名、本地路径、候选或系统流程。",
    "不要调用 choose_sticker。",
  ].join("\n");
}

function buildExplicitStickerUnavailableContext(): string {
  return [
    "[Yogurt Sticker Request]",
    "用户明确要求发送表情包，但本轮没有找到足够相关且安全的候选。",
    "只回复一句很短、自然的话说明这次没有发出来，建议不超过 15 个汉字。",
    "绝对不要描述、解释、复述或虚构任何表情包画面，也不要假装已经发送图片。",
    "不要提及表情包 ID、文件名、本地路径、候选、评分或系统流程。",
  ].join("\n");
}

function normalizePayloadMediaUrls(payload: any): string[] {
  const urls: string[] = [];
  if (Array.isArray(payload?.mediaUrls)) {
    for (const value of payload.mediaUrls) {
      const resolved = optionalString(value);
      if (resolved) urls.push(resolved);
    }
  }
  const single = optionalString(payload?.mediaUrl);
  if (single) urls.push(single);
  return [...new Set(urls)];
}

const chooseStickerParameters = {
  type: "object",
  additionalProperties: false,
  required: ["sticker_id"],
  properties: {
    sticker_id: {
      type: "string",
      minLength: 3,
      maxLength: 80,
      description: "本轮 [Yogurt Sticker Candidates] 中列出的 sticker_id",
    },
  },
};

const chooseStickerOutput = {
  type: "object",
  additionalProperties: false,
  required: ["status"],
  properties: {
    status: { type: "string", enum: ["selected", "rejected"] },
    sticker_id: { type: "string" },
    reason: { type: "string" },
    idempotent: { type: "boolean" },
  },
};

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Yogurt Social Injector",
  description: "Injects Yogurt social context and delivers bounded V2.1 stickers.",
  register(api) {
    if (typeof api.registerTool === "function") {
      api.registerTool((toolContext: any) => {
        const config = readConfig(api);
        if (config.stickerEnabled && config.stickerSelectionMode !== "tool") {
          return null;
        }
        return {
          name: "choose_sticker",
          label: "Choose Yogurt Sticker",
          description:
            "选择本轮候选列表中的一张表情包。可以不调用；每轮最多选择一张。最终文字回复发送后，系统会通过当前频道的媒体适配器发送所选图片。",
          parameters: chooseStickerParameters as any,
          outputSchema: chooseStickerOutput as any,
          async execute(_toolCallId: string, params: Record<string, unknown>) {
            const channel = channelFromContext(toolContext);
            const senderId = senderIdFromContext(toolContext);
            const aliases = addPeerAlias(
              contextAliases(toolContext, channel, senderId),
              channel,
              senderId,
            );
            const stickerId = optionalString(params.sticker_id);
            if (!stickerId || !STICKER_ID_PATTERN.test(stickerId)) {
              return {
                content: [
                  {
                    type: "text",
                    text: "表情包选择被拒绝：sticker_id 无效。请继续完成纯文本回复。",
                  },
                ],
                details: { status: "rejected", reason: "invalid_sticker_id" },
              };
            }
            const result = selectionStore.select(aliases, stickerId, Date.now(), {
              sessionKey: sessionKeyFromContext(toolContext),
              policyKey: preferredPolicyKey(aliases),
              mode: "tool",
              explicit: false,
            });
            if (result.status === "rejected") {
              return {
                content: [
                  {
                    type: "text",
                    text: "表情包选择未生效：该 ID 不属于当前有效候选，或候选已过期。请继续完成纯文本回复。",
                  },
                ],
                details: { status: "rejected", reason: result.reason },
              };
            }
            api.logger.info(`${PLUGIN_ID}: sticker selection pending mode=tool`);
            return {
              content: [
                {
                  type: "text",
                  text: "已记录本轮表情包选择。请继续完成文本回复，不要在回复中输出表情包 ID。",
                },
              ],
              details: {
                status: "selected",
                sticker_id: result.sticker.id,
                idempotent: result.idempotent,
              },
            };
          },
        };
      });
    } else {
      api.logger.warn(`${PLUGIN_ID}: registerTool unavailable; sticker tool mode disabled`);
    }

    api.on(
      "before_prompt_build",
      async (event, ctx) => {
        if (isNativeVoiceContext(ctx, event)) return;
        const config = readConfig(api, event);
        if (!config.enabled) return;
        const prompt = typeof event.prompt === "string" ? event.prompt : "";
        if (!prompt) return;
        const channel = channelFromContext(ctx);
        if (!channelIsAllowed(config, channel) || ctx?.jobId) return;
        const senderId = senderIdFromContext(ctx);
        const userId = resolveUserId(config, senderId, channel);
        if (!userId) return;

        const aliases = addPeerAlias(
          contextAliases(ctx, channel, senderId),
          channel,
          senderId,
        );
        const policyKey = preferredPolicyKey(aliases);
        const explicit = messageExplicitlyRequestsSticker(prompt);
        const forbidden = messageForbidsSticker(prompt);
        const startedAt = Date.now();
        api.logger.info(
          `${PLUGIN_ID}: injection start channel=${channel} promptChars=${prompt.length}`,
        );

        try {
          const bridgeResult = await runBridge(
            config,
            {
              user_id: userId,
              message: prompt,
              assistant_reply: "",
              decision_phase: "pre_reply",
              explicit_sticker_request: explicit,
              sticker_forbidden: forbidden,
            } as any,
          );
          const rawSocialContext =
            typeof bridgeResult.social_context === "string"
              ? bridgeResult.social_context.trim()
              : "";
          const candidates = config.stickerEnabled
            ? parseStickerCandidates(bridgeResult.sticker_candidates)
            : [];
          const bridgeDecision = parseStickerBridgeDecision(
            (bridgeResult as any).sticker_decision,
          );

          let socialContext = rawSocialContext;
          let decisionReason = "disabled";
          if (!config.stickerEnabled) {
            selectionStore.clear(aliases);
            turnStore.clear(aliases);
          } else if (config.stickerSelectionMode === "tool") {
            turnStore.clear(aliases);
            if (candidates.length > 0) {
              selectionStore.register(aliases, candidates);
              decisionReason = "tool_candidates";
            } else {
              selectionStore.clear(aliases);
              decisionReason = "no_candidates";
            }
          } else {
            socialContext = stripStickerCandidateContext(rawSocialContext);
            selectionStore.clear(aliases);
            if (forbidden) {
              turnStore.clear(aliases);
              decisionReason = "user_forbid";
            } else {
              turnStore.register(aliases, {
                userId,
                message: prompt,
                explicit,
                forbidden,
                policyKey,
                sessionKey: sessionKeyFromContext(ctx),
              });
              decisionReason = explicit ? bridgeDecision.mode : "deferred_post_reply";
              if (explicit) {
                socialContext = [
                  socialContext,
                  bridgeDecision.shouldSend && candidates.length > 0
                    ? buildExplicitStickerAvailableContext()
                    : buildExplicitStickerUnavailableContext(),
                ]
                  .filter(Boolean)
                  .join("\n\n");
                if (!bridgeDecision.shouldSend || candidates.length === 0) {
                  turnStore.clear(aliases);
                }
              }
            }
          }

          api.logger.info(
            `${PLUGIN_ID}: injection ready bridgeMs=${Date.now() - startedAt} contextChars=${socialContext.length} stickerMode=${config.stickerSelectionMode} stickerCandidates=${candidates.length} stickerDecision=${decisionReason} stickerExplicit=${explicit}`,
          );
          if (!socialContext) return;
          return { prependContext: socialContext };
        } catch (error) {
          selectionStore.clear(aliases);
          turnStore.clear(aliases);
          const reason = error instanceof Error ? error.message : "unknown_error";
          api.logger.warn(`${PLUGIN_ID}: social layer unavailable (${reason}); fail-open`);
          return;
        }
      },
      { priority: 60, timeoutMs: 12000 },
    );

    const finalizeAutoSelection = async (params: {
      trigger: "agent_end" | "message_sent" | "reply_payload_sending";
      aliases: string[];
      assistantText?: string;
      ctx: any;
      event?: any;
    }): Promise<boolean> => {
      const config = readConfig(api, params.event);
      if (
        !config.enabled ||
        !config.stickerEnabled ||
        config.stickerSelectionMode !== "auto"
      ) {
        return false;
      }
      if (selectionStore.getPending(params.aliases)) return true;
      const turn = turnStore.get(params.aliases);
      if (!turn) return false;
      if (!params.assistantText && !turn.explicit) return false;

      try {
        const bridgeResult = await runBridge(
          config,
          {
            user_id: turn.userId,
            message: turn.message,
            assistant_reply: params.assistantText || "",
            decision_phase: "post_reply",
            explicit_sticker_request: turn.explicit,
            sticker_forbidden: turn.forbidden,
          } as any,
        );
        const candidates = parseStickerCandidates(bridgeResult.sticker_candidates);
        const bridgeDecision = parseStickerBridgeDecision(
          (bridgeResult as any).sticker_decision,
        );
        api.logger.info(
          `${PLUGIN_ID}: send decision trigger=${params.trigger} ${compactDecisionLog(bridgeDecision)} candidates=${candidates.length} top=${candidates[0]?.id || "none"}:${candidates[0]?.score || 0} runnerUp=${candidates[1]?.id || "none"}:${candidates[1]?.score || 0}`,
        );
        turnStore.consume(params.aliases);
        if (!bridgeDecision.shouldSend || candidates.length === 0) {
          selectionStore.clear(params.aliases);
          return false;
        }

        const deliveryConfig = readStickerDeliveryConfig(config.stickerConfigPath);
        const policyDecision = autoPolicyStore.choose(
          turn.policyKey,
          candidates,
          turn.explicit,
          deliveryConfig,
        );
        if (!policyDecision.selected) {
          api.logger.info(
            `${PLUGIN_ID}: send decision rejected by delivery policy reason=${policyDecision.reason}`,
          );
          selectionStore.clear(params.aliases);
          return false;
        }

        const resolvedAliases = [...new Set([...turn.aliases, ...params.aliases])];
        selectionStore.register(resolvedAliases, candidates);
        const selected = selectionStore.select(
          resolvedAliases,
          policyDecision.selected.id,
          Date.now(),
          {
            sessionKey: turn.sessionKey,
            policyKey: turn.policyKey,
            mode: "auto",
            explicit: turn.explicit,
          },
        );
        if (selected.status !== "selected") {
          selectionStore.clear(resolvedAliases);
          api.logger.warn(
            `${PLUGIN_ID}: post-reply selection rejected reason=${selected.reason}`,
          );
          return false;
        }
        api.logger.info(
          `${PLUGIN_ID}: post-reply sticker selected sticker=${selected.sticker.id} reason=${policyDecision.reason}`,
        );
        return true;
      } catch (error) {
        const reason = error instanceof Error ? error.message : "unknown_error";
        api.logger.warn(
          `${PLUGIN_ID}: post-reply sticker decision unavailable trigger=${params.trigger} (${reason}); text preserved`,
        );
        if (params.trigger !== "agent_end") turnStore.clear(params.aliases);
        return false;
      }
    };

    const scheduleWeixinStickerDelivery = (params: {
      trigger: "agent_end" | "message_sent";
      aliases: string[];
      ctx: any;
      event?: any;
      minimumDelayMs?: number;
    }) => {
      const config = readConfig(api);
      if (!config.enabled || !config.stickerEnabled) return;
      const pending = selectionStore.getPending(params.aliases);
      if (!pending) return;
      if (deliveryTimers.has(pending.snapshotId)) return;

      const target = targetFromContext(params.ctx, params.event);
      if (!target) {
        api.logger.warn(
          `${PLUGIN_ID}: sticker outbound delivery skipped (target_missing)`,
        );
        return;
      }
      const accountId =
        accountIdFromContext(params.ctx) || optionalString(params.event?.accountId);
      const deliveryConfig = readStickerDeliveryConfig(config.stickerConfigPath);
      const delayMs = Math.max(
        deliveryConfig.deliveryDelayMs,
        params.minimumDelayMs || 0,
      );
      api.logger.info(
        `${PLUGIN_ID}: sticker outbound scheduled trigger=${params.trigger} mode=${pending.mode} hasTarget=true delayMs=${delayMs}`,
      );

      const timer = setTimeout(async () => {
        deliveryTimers.delete(pending.snapshotId);
        const selected = selectionStore.consumePendingBySnapshot(pending.snapshotId);
        if (!selected) return;
        try {
          const filePath = resolveStickerFile(config, selected.sticker);
          const fileSize = statSync(filePath).size;
          if (fileSize > deliveryConfig.maxBytes) {
            throw new Error("sticker_file_too_large");
          }
          const loadAdapter = api.runtime?.channel?.outbound?.loadAdapter;
          if (typeof loadAdapter !== "function") {
            throw new Error("channel_outbound_adapter_unavailable");
          }
          const adapter = await loadAdapter(WEIXIN_CHANNEL);
          if (typeof adapter?.sendMedia !== "function") {
            throw new Error("weixin_send_media_unavailable");
          }
          await adapter.sendMedia({
            cfg: api.config,
            to: target,
            text: "",
            mediaUrl: filePath,
            ...(accountId ? { accountId } : {}),
          } as any);
          autoPolicyStore.recordSent(selected.policyKey, selected.sticker);
          api.logger.info(
            `${PLUGIN_ID}: sticker media sent trigger=channel_outbound_adapter mode=${selected.mode} sticker=${selected.sticker.id}`,
          );
        } catch (error) {
          const reason = error instanceof Error ? error.message : "unknown_error";
          api.logger.warn(
            `${PLUGIN_ID}: sticker media send failed trigger=channel_outbound_adapter (${reason}); text preserved`,
          );
        }
      }, delayMs);
      deliveryTimers.set(pending.snapshotId, timer);
    };

    api.on("agent_end", async (event: any, ctx: any) => {
      if (event?.success !== true) return;
      const channel = channelFromContext(ctx);
      if (channel !== WEIXIN_CHANNEL) return;
      const senderId = senderIdFromContext(ctx);
      const peerId = targetFromContext(ctx, event);
      const aliases = addPeerAlias(
        contextAliases(ctx, channel, senderId, event),
        channel,
        peerId,
      );
      await finalizeAutoSelection({
        trigger: "agent_end",
        aliases,
        assistantText: assistantTextFromEvent(event, ctx),
        ctx,
        event,
      });
      scheduleWeixinStickerDelivery({
        trigger: "agent_end",
        aliases,
        ctx,
        event,
        minimumDelayMs: AGENT_END_DELIVERY_DELAY_MS,
      });
    });

    api.on("message_sent", async (event: any, ctx: any) => {
      if (event?.success !== true || !optionalString(event?.content)) return;
      const channel = channelFromContext(ctx);
      if (channel !== WEIXIN_CHANNEL) return;
      const senderId = senderIdFromContext(ctx);
      const peerId = targetFromContext(ctx, event);
      const aliases = addPeerAlias(
        contextAliases(ctx, channel, senderId, event),
        channel,
        peerId,
      );
      await finalizeAutoSelection({
        trigger: "message_sent",
        aliases,
        assistantText: optionalString(event.content),
        ctx,
        event,
      });
      scheduleWeixinStickerDelivery({
        trigger: "message_sent",
        aliases,
        ctx,
        event,
      });
    });

    api.on(
      "reply_payload_sending",
      async (event: any, ctx: any) => {
        if (event?.kind !== "final") return;
        const config = readConfig(api, event);
        if (!config.enabled || !config.stickerEnabled) return;
        const channel = optionalString(event?.channel) || channelFromContext(ctx);
        if (!channelIsAllowed(config, channel)) return;
        if (channel === WEIXIN_CHANNEL) return;

        const senderId = senderIdFromContext(ctx);
        const peerId = targetFromContext(ctx, event);
        const aliases = addPeerAlias(
          contextAliases(ctx, channel, senderId, event),
          channel,
          peerId,
        );
        await finalizeAutoSelection({
          trigger: "reply_payload_sending",
          aliases,
          assistantText: assistantTextFromEvent(event, ctx),
          ctx,
          event,
        });
        const pending = selectionStore.getPending(aliases);
        if (!pending) return;

        let filePath: string;
        try {
          filePath = resolveStickerFile(config, pending.sticker);
        } catch (error) {
          selectionStore.consumePendingBySnapshot(pending.snapshotId);
          const reason = error instanceof Error ? error.message : "unknown_error";
          api.logger.warn(
            `${PLUGIN_ID}: sticker media attach failed (${reason}); text preserved`,
          );
          return;
        }
        const selected = selectionStore.consumePendingBySnapshot(pending.snapshotId);
        if (!selected) return;
        const mediaUrls = normalizePayloadMediaUrls(event?.payload);
        if (!mediaUrls.includes(filePath)) mediaUrls.push(filePath);
        autoPolicyStore.recordSent(selected.policyKey, selected.sticker);
        api.logger.info(
          `${PLUGIN_ID}: sticker media attached trigger=reply_payload_sending mode=${selected.mode} sticker=${selected.sticker.id} mediaCount=${mediaUrls.length}`,
        );
        return {
          payload: {
            ...event.payload,
            mediaUrl: undefined,
            mediaUrls,
          },
        };
      },
      { priority: 40, timeoutMs: 12000 },
    );

    api.logger.info(
      `${PLUGIN_ID}: registered before_prompt_build, agent_end, message_sent, reply_payload_sending, V2.1 post-reply decision, and optional choose_sticker`,
    );
  },
});
