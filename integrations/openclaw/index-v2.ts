import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import {
  StickerAutoPolicyStore,
  StickerSelectionStore,
} from "./sticker-selection.ts";
import {
  buildAutoStickerContext,
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
const deliveryTimers = new Map<string, NodeJS.Timeout>();

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
            "选择本轮候选列表中的一张表情包。可以不调用；每轮最多选择一张。最终文字回复发送后，系统会自动发送所选图片。",
          parameters: chooseStickerParameters as any,
          outputSchema: chooseStickerOutput as any,
          async execute(_toolCallId: string, params: Record<string, unknown>) {
            const channel = channelFromContext(toolContext);
            const senderId = senderIdFromContext(toolContext);
            const aliases = contextAliases(toolContext, channel, senderId);
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

        const aliases = contextAliases(ctx, channel, senderId);
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
                  socialContext = [socialContext, buildAutoStickerContext(selected.sticker)]
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

    if (typeof api.session?.workflow?.sendSessionAttachment === "function") {
      api.on("message_sent", (event: any, ctx: any) => {
        if (event?.success !== true || !optionalString(event?.content)) return;
        const config = readConfig(api);
        if (!config.enabled || !config.stickerEnabled) return;

        const channel = channelFromContext(ctx);
        const senderId = senderIdFromContext(ctx);
        const aliases = contextAliases(ctx, channel, senderId, event);
        const pending = selectionStore.getPending(aliases);
        if (!pending) return;

        const existingTimer = deliveryTimers.get(pending.snapshotId);
        if (existingTimer) clearTimeout(existingTimer);
        const deliveryConfig = readStickerDeliveryConfig(config.stickerConfigPath);
        const timer = setTimeout(async () => {
          deliveryTimers.delete(pending.snapshotId);
          const selected = selectionStore.consumePendingBySnapshot(pending.snapshotId);
          if (!selected) return;
          if (!selected.sessionKey) {
            api.logger.warn(`${PLUGIN_ID}: sticker delivery skipped (session_key_missing)`);
            return;
          }

          try {
            const filePath = resolveStickerFile(config, selected.sticker);
            await api.session.workflow.sendSessionAttachment({
              sessionKey: selected.sessionKey,
              files: [{ path: filePath }],
              maxBytes: deliveryConfig.maxBytes,
            });
            autoPolicyStore.recordSent(selected.policyKey, selected.sticker);
            api.logger.info(
              `${PLUGIN_ID}: sticker media sent mode=${selected.mode} sticker=${selected.sticker.id}`,
            );
          } catch (error) {
            const reason = error instanceof Error ? error.message : "unknown_error";
            api.logger.warn(
              `${PLUGIN_ID}: sticker media send failed (${reason}); text preserved`,
            );
          }
        }, deliveryConfig.deliveryDelayMs);
        deliveryTimers.set(pending.snapshotId, timer);
      });
    } else {
      api.logger.warn(
        `${PLUGIN_ID}: sendSessionAttachment unavailable; sticker media delivery disabled`,
      );
    }

    api.logger.info(
      `${PLUGIN_ID}: registered before_prompt_build, message_sent, and optional choose_sticker`,
    );
  },
});
