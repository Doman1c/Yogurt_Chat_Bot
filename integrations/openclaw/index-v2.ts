import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import {
  StickerAutoPolicyStore,
  StickerSelectionStore,
} from "./sticker-selection.ts";
import {
  channelFromContext,
  channelIsAllowed,
  contextAliases,
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
const STICKER_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$/;

const selectionStore = new StickerSelectionStore();
const autoPolicyStore = new StickerAutoPolicyStore();

function addPeerAlias(
  aliases: string[],
  channel: string,
  peerId: unknown,
): string[] {
  const resolvedPeerId = optionalString(peerId);
  if (!resolvedPeerId) return aliases;
  return [...new Set([...aliases, `peer:${channel}:${resolvedPeerId}`])];
}

function buildConciseAutoStickerContext(): string {
  return [
    "[Yogurt Sticker Delivery]",
    "系统会在本轮回复中自动附上一张合适的表情包。",
    "正文只需要一句很短、自然的确认（建议不超过 20 个汉字）或一个可爱颜文字。",
    "不要描述、解释或复述表情包画面，也不要提及表情包 ID、文件名、本地路径、候选或系统流程。",
    "不要调用 choose_sticker。",
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
  description: "Injects Yogurt social context and delivers bounded V2 stickers.",
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
            "选择本轮候选列表中的一张表情包。可以不调用；每轮最多选择一张。最终回复会通过正常媒体分发链路附上所选图片。",
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
        const startedAt = Date.now();
        api.logger.info(
          `${PLUGIN_ID}: injection start channel=${channel} promptChars=${prompt.length}`,
        );

        try {
          const bridgeResult = await runBridge(config, {
            user_id: userId,
            message: prompt,
          });
          const rawSocialContext =
            typeof bridgeResult.social_context === "string"
              ? bridgeResult.social_context.trim()
              : "";
          const candidates = config.stickerEnabled
            ? parseStickerCandidates(bridgeResult.sticker_candidates)
            : [];

          let socialContext = rawSocialContext;
          let selectedId = "none";
          let decisionReason = "disabled";

          if (!config.stickerEnabled) {
            selectionStore.clear(aliases);
          } else if (config.stickerSelectionMode === "tool") {
            if (candidates.length > 0) {
              selectionStore.register(aliases, candidates);
              decisionReason = "tool_candidates";
            } else {
              selectionStore.clear(aliases);
              decisionReason = "no_candidates";
            }
          } else {
            socialContext = stripStickerCandidateContext(rawSocialContext);
            const explicit = messageExplicitlyRequestsSticker(prompt);
            if (messageForbidsSticker(prompt)) {
              selectionStore.clear(aliases);
              decisionReason = "user_forbid";
            } else {
              const deliveryConfig = readStickerDeliveryConfig(config.stickerConfigPath);
              const decision = autoPolicyStore.choose(
                policyKey,
                candidates,
                explicit,
                deliveryConfig,
              );
              decisionReason = decision.reason;
              if (decision.selected) {
                selectionStore.register(aliases, candidates);
                const selected = selectionStore.select(
                  aliases,
                  decision.selected.id,
                  Date.now(),
                  {
                    sessionKey: sessionKeyFromContext(ctx),
                    policyKey,
                    mode: "auto",
                    explicit,
                  },
                );
                if (selected.status === "selected") {
                  selectedId = selected.sticker.id;
                  socialContext = [socialContext, buildConciseAutoStickerContext()]
                    .filter(Boolean)
                    .join("\n\n");
                } else {
                  selectionStore.clear(aliases);
                  decisionReason = selected.reason;
                }
              } else {
                selectionStore.clear(aliases);
              }
            }
          }

          if (!socialContext) {
            api.logger.info(
              `${PLUGIN_ID}: bridge returned no context in ${Date.now() - startedAt}ms`,
            );
            return;
          }

          api.logger.info(
            `${PLUGIN_ID}: injection ready bridgeMs=${Date.now() - startedAt} contextChars=${socialContext.length} stickerMode=${config.stickerSelectionMode} stickerCandidates=${candidates.length} stickerSelected=${selectedId} stickerDecision=${decisionReason}`,
          );
          return { prependContext: socialContext };
        } catch (error) {
          selectionStore.clear(aliases);
          const reason = error instanceof Error ? error.message : "unknown_error";
          api.logger.warn(`${PLUGIN_ID}: social layer unavailable (${reason}); fail-open`);
          return;
        }
      },
      { priority: 60, timeoutMs: 12000 },
    );

    api.on(
      "reply_payload_sending",
      async (event: any, ctx: any) => {
        if (event?.kind !== "final") return;

        const config = readConfig(api, event);
        if (!config.enabled || !config.stickerEnabled) return;

        const channel = optionalString(event?.channel) || channelFromContext(ctx);
        if (!channelIsAllowed(config, channel)) return;

        const senderId = senderIdFromContext(ctx);
        const peerId =
          senderId ||
          optionalString(ctx?.conversationId) ||
          optionalString(ctx?.channelId);
        const aliases = addPeerAlias(
          contextAliases(ctx, channel, senderId, event),
          channel,
          peerId,
        );
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
      { priority: 40, timeoutMs: 5000 },
    );

    api.logger.info(
      `${PLUGIN_ID}: registered before_prompt_build, reply_payload_sending, and optional choose_sticker`,
    );
  },
});
