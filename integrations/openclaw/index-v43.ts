import { AsyncLocalStorage } from "node:async_hooks";

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import v4Plugin from "./index-v4.ts";
import { splitNaturalBubbles } from "./multi-bubble.ts";
import {
  compareOutboundResult,
  formatOutboundComparison,
  outboundTurns,
  type OutboundIntent,
  type OutboundResult,
} from "./outbound-coordinator.ts";
import {
  asRecord,
  channelFromContext,
  contextAliases,
  optionalString,
  senderIdFromContext,
} from "./sticker-plugin-utils.ts";

const PLUGIN_ID = "yogurt-social-injector";
const WEIXIN_CHANNEL = "openclaw-weixin";
const RESPONSE_PLAN_MARKER = "[Yogurt Response Plan V4.1]";

type HookHandler = (event: any, ctx: any) => unknown | Promise<unknown>;
type DeliveryScope = "legacy_child" | "coordinator_child";

type V43Config = {
  unifiedOutboundShadowEnabled: boolean;
  unifiedOutboundEnforceEnabled: boolean;
  multiBubbleMinChars: number;
  multiBubbleMaxChars: number;
  multiBubbleDelayMs: number;
};

type PlannedDelivery = {
  bubbles?: string[];
  fallbackReason?: string;
};

type DirectDeliveryResult = {
  hookResult?: Record<string, unknown>;
  result?: OutboundResult;
  fallbackReason?: string;
};

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function integerValue(
  value: unknown,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  return typeof value === "number" && Number.isInteger(value)
    ? Math.min(maximum, Math.max(minimum, value))
    : fallback;
}

function readV43Config(api: any, event?: any): V43Config {
  const raw = {
    ...asRecord(api.pluginConfig),
    ...asRecord(asRecord(event?.context).pluginConfig),
  };
  return {
    unifiedOutboundShadowEnabled: booleanValue(
      raw.unifiedOutboundShadowEnabled,
      false,
    ),
    unifiedOutboundEnforceEnabled: booleanValue(
      raw.unifiedOutboundEnforceEnabled,
      false,
    ),
    multiBubbleMinChars: integerValue(raw.multiBubbleMinChars, 24, 8, 200),
    multiBubbleMaxChars: integerValue(raw.multiBubbleMaxChars, 360, 80, 2000),
    multiBubbleDelayMs: integerValue(raw.multiBubbleDelayMs, 420, 0, 3000),
  };
}

function coordinatorEnabled(config: V43Config): boolean {
  return config.unifiedOutboundShadowEnabled || config.unifiedOutboundEnforceEnabled;
}

function eventMetadata(event: any): Record<string, unknown> {
  return asRecord(event?.metadata);
}

function eventChannel(event: any, ctx: any): string {
  return (
    optionalString(event?.channel) ||
    optionalString(eventMetadata(event).channel) ||
    channelFromContext(ctx)
  );
}

function targetFromContext(event: any, ctx: any): string | undefined {
  return (
    optionalString(event?.to) ||
    optionalString(ctx?.to) ||
    optionalString(ctx?.chatId) ||
    optionalString(ctx?.conversationId) ||
    optionalString(ctx?.channelContext?.chat?.id) ||
    optionalString(ctx?.deliveryContext?.to) ||
    senderIdFromContext(ctx)
  );
}

function accountIdFromContext(event: any, ctx: any): string | undefined {
  const metadata = eventMetadata(event);
  return (
    optionalString(event?.accountId) ||
    optionalString(metadata.accountId) ||
    optionalString(ctx?.accountId) ||
    optionalString(ctx?.channelContext?.accountId) ||
    optionalString(ctx?.deliveryContext?.accountId)
  );
}

function eventHasMedia(event: any): boolean {
  const metadata = eventMetadata(event);
  return Boolean(
    optionalString(event?.mediaUrl) ||
      (Array.isArray(event?.mediaUrls) && event.mediaUrls.length > 0) ||
      optionalString(metadata.mediaUrl) ||
      (Array.isArray(metadata.mediaUrls) && metadata.mediaUrls.length > 0),
  );
}

function aliasesFor(event: any, ctx: any): string[] {
  const channel = eventChannel(event, ctx);
  const senderId = senderIdFromContext(ctx);
  const target = targetFromContext(event, ctx);
  const accountId = accountIdFromContext(event, ctx);
  const aliases = contextAliases(ctx, channel, senderId, event);
  if (target) {
    aliases.push(`peer:${channel}:${target}`);
    aliases.push(`value:${target}`);
    if (accountId) aliases.push(`account:${accountId}:${target}`);
  }
  return [...new Set(aliases.filter(Boolean))];
}

function parseReplyLength(value: string): string {
  if (value.includes("很短")) return "very_short";
  if (value.includes("简短")) return "short";
  if (value.includes("详细")) return "detailed";
  return "medium";
}

function parseStickerPreference(value: string): string {
  if (value.includes("不建议")) return "none";
  if (value.includes("庆祝")) return "celebration";
  if (value.includes("安慰")) return "comfort";
  if (value.includes("轻松")) return "playful";
  return "neutral";
}

function responsePlanFromHookResult(value: unknown): Record<string, unknown> | undefined {
  const result = asRecord(value);
  const context = optionalString(result.prependContext);
  if (!context || !context.startsWith(RESPONSE_PLAN_MARKER)) return undefined;
  const bubbleMatch = context.match(/建议气泡数：(\d+)/);
  const lengthMatch = context.match(/建议长度：([^\n]+)/);
  const stickerMatch = context.match(/表情倾向：([^\n]+)/);
  return {
    plan_version: "4.1",
    bubble_target: bubbleMatch ? Number.parseInt(bubbleMatch[1], 10) : 1,
    reply_length: parseReplyLength(lengthMatch?.[1] || ""),
    sticker_preference: parseStickerPreference(stickerMatch?.[1] || ""),
    state_version: 0,
  };
}

function outboundResult(event: any, hookResult: unknown): OutboundResult {
  const result = asRecord(hookResult);
  const metadata = asRecord(result.metadata);
  const text = optionalString(event?.content) || optionalString(event?.text) || "";
  if (eventHasMedia(event)) {
    return {
      actualBubbleCount: 1,
      textChars: text.length,
      outcome: "media_passthrough",
      stickerOutcome: "unknown",
    };
  }
  const cancelReason = optionalString(result.cancelReason) || "";
  if (
    result.cancel === true &&
    (cancelReason === "yogurt_multi_bubble_partial" ||
      cancelReason === "yogurt_unified_outbound_partial")
  ) {
    const sentCount =
      typeof metadata.sentCount === "number" && Number.isInteger(metadata.sentCount)
        ? Math.max(1, metadata.sentCount)
        : 1;
    return {
      actualBubbleCount: sentCount,
      textChars: text.length,
      outcome: "partial",
      stickerOutcome: "unknown",
    };
  }
  if (
    result.cancel === true &&
    (cancelReason === "yogurt_multi_bubble" ||
      cancelReason === "yogurt_unified_outbound")
  ) {
    const bubbleCount =
      typeof metadata.bubbleCount === "number" && Number.isInteger(metadata.bubbleCount)
        ? Math.max(1, metadata.bubbleCount)
        : 1;
    return {
      actualBubbleCount: bubbleCount,
      textChars: text.length,
      outcome: bubbleCount > 1 ? "split" : "single",
      stickerOutcome: "unknown",
    };
  }
  return {
    actualBubbleCount: 1,
    textChars: text.length,
    outcome: "single",
    stickerOutcome: "unknown",
  };
}

function structuredFallbackReason(text: string): string | undefined {
  if (text.includes("```")) return "code_block_passthrough";
  if (/https?:\/\//i.test(text)) return "link_passthrough";
  if (/^\s*(?:[-*]|\d+[.)])\s+/m.test(text)) return "list_passthrough";
  if (/^\s*#{1,6}\s+/m.test(text) || text.includes("|---")) {
    return "structured_text_passthrough";
  }
  return undefined;
}

function planDelivery(
  event: any,
  text: string,
  intent: OutboundIntent,
  config: V43Config,
): PlannedDelivery {
  if (eventHasMedia(event)) return { fallbackReason: "media_passthrough" };
  if (intent.bubbleTarget === 1) return { bubbles: [text] };
  const structuredReason = structuredFallbackReason(text);
  if (structuredReason) return { fallbackReason: structuredReason };
  if (text.length < config.multiBubbleMinChars) {
    return { fallbackReason: "text_below_split_minimum" };
  }
  if (text.length > config.multiBubbleMaxChars) {
    return { fallbackReason: "text_above_split_maximum" };
  }
  const bubbles = splitNaturalBubbles(text, {
    minChars: config.multiBubbleMinChars,
    maxChars: config.multiBubbleMaxChars,
    maxBubbles: intent.bubbleTarget,
  });
  if (bubbles.length !== intent.bubbleTarget) {
    return { fallbackReason: "natural_boundary_count_mismatch" };
  }
  return { bubbles };
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function appendComparisonReason(
  intent: OutboundIntent,
  result: OutboundResult,
  reason: string,
): string {
  const comparison = compareOutboundResult(intent, result);
  if (reason && !comparison.reasons.includes(reason)) comparison.reasons.push(reason);
  return formatOutboundComparison(comparison);
}

async function sendPlannedBubbles(
  api: any,
  deliveryScope: AsyncLocalStorage<DeliveryScope>,
  event: any,
  ctx: any,
  bubbles: string[],
  config: V43Config,
): Promise<DirectDeliveryResult> {
  const to = targetFromContext(event, ctx);
  if (!to) return { fallbackReason: "target_missing" };
  const channel = eventChannel(event, ctx);
  if (channel !== WEIXIN_CHANNEL && !to.endsWith("@im.wechat")) {
    return { fallbackReason: "channel_not_supported" };
  }
  const loadAdapter = api.runtime?.channel?.outbound?.loadAdapter;
  if (typeof loadAdapter !== "function") {
    return { fallbackReason: "adapter_loader_unavailable" };
  }
  const adapter = await loadAdapter(WEIXIN_CHANNEL);
  if (typeof adapter?.sendText !== "function") {
    return { fallbackReason: "adapter_send_text_unavailable" };
  }
  const accountId = accountIdFromContext(event, ctx);
  const text = optionalString(event?.content) || optionalString(event?.text) || "";
  let sentCount = 0;
  try {
    for (let index = 0; index < bubbles.length; index += 1) {
      const bubble = bubbles[index];
      await deliveryScope.run("coordinator_child", () =>
        adapter.sendText({
          cfg: api.config,
          to,
          text: bubble,
          ...(accountId ? { accountId } : {}),
        } as any),
      );
      sentCount += 1;
      if (index < bubbles.length - 1 && config.multiBubbleDelayMs > 0) {
        await sleep(config.multiBubbleDelayMs);
      }
    }
    return {
      hookResult: {
        cancel: true,
        cancelReason: "yogurt_unified_outbound",
        metadata: { bubbleCount: bubbles.length },
      },
      result: {
        actualBubbleCount: bubbles.length,
        textChars: text.length,
        outcome: bubbles.length > 1 ? "split" : "single",
        stickerOutcome: "unknown",
      },
    };
  } catch {
    if (sentCount === 0) return { fallbackReason: "adapter_send_failed_before_first" };
    return {
      hookResult: {
        cancel: true,
        cancelReason: "yogurt_unified_outbound_partial",
        metadata: { bubbleCount: bubbles.length, sentCount },
      },
      result: {
        actualBubbleCount: sentCount,
        textChars: text.length,
        outcome: "partial",
        stickerOutcome: "unknown",
      },
    };
  }
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Yogurt Social Injector",
  description:
    "Adds opt-in V4.3 shadow and enforced outbound coordination on top of V4.0-V4.2 and the V3 delivery path.",
  register(api: any) {
    const deliveryScope = new AsyncLocalStorage<DeliveryScope>();
    const wrappedApi = Object.create(api);

    Object.defineProperty(wrappedApi, "on", {
      configurable: true,
      enumerable: true,
      writable: false,
      value(name: string, handler: HookHandler, options?: unknown) {
        const priority = asRecord(options).priority;

        if (name === "before_prompt_build" && priority === 50) {
          const wrapped: HookHandler = async (event, ctx) => {
            const result = await handler(event, ctx);
            const config = readV43Config(api, event);
            if (!coordinatorEnabled(config)) return result;
            const plan = responsePlanFromHookResult(result);
            if (!plan) return result;
            const intent = outboundTurns.register(aliasesFor(event, ctx), plan);
            if (intent) {
              api.logger.info(
                `${PLUGIN_ID}: v4.3 outbound intent ready targetBubbles=${intent.bubbleTarget} replyLength=${intent.replyLength} sticker=${intent.stickerPreference} stateVersion=${intent.stateVersion}`,
              );
            }
            return result;
          };
          return api.on(name, wrapped, options);
        }

        if (name === "message_sending" && priority === 70) {
          const wrapped: HookHandler = async (event, ctx) => {
            const scope = deliveryScope.getStore();
            if (scope === "coordinator_child") return undefined;
            if (scope === "legacy_child") return handler(event, ctx);

            const config = readV43Config(api, event);
            if (!coordinatorEnabled(config)) return handler(event, ctx);
            const intent = outboundTurns.consume(aliasesFor(event, ctx));
            if (!intent) return handler(event, ctx);

            const text = optionalString(event?.content) || optionalString(event?.text) || "";
            if (config.unifiedOutboundEnforceEnabled && text) {
              const planned = planDelivery(event, text, intent, config);
              if (planned.bubbles) {
                if (planned.bubbles.length === 1) {
                  const result: OutboundResult = {
                    actualBubbleCount: 1,
                    textChars: text.length,
                    outcome: "single",
                    stickerOutcome: "unknown",
                  };
                  api.logger.info(
                    `${PLUGIN_ID}: v4.3 outbound enforced ${appendComparisonReason(intent, result, "enforced_target_applied")}`,
                  );
                  return undefined;
                }
                const direct = await sendPlannedBubbles(
                  api,
                  deliveryScope,
                  event,
                  ctx,
                  planned.bubbles,
                  config,
                );
                if (direct.result && direct.hookResult) {
                  const reason =
                    direct.result.outcome === "partial"
                      ? "enforced_partial_delivery"
                      : "enforced_target_applied";
                  api.logger.info(
                    `${PLUGIN_ID}: v4.3 outbound enforced ${appendComparisonReason(intent, direct.result, reason)}`,
                  );
                  return direct.hookResult;
                }
                planned.fallbackReason = direct.fallbackReason || "direct_delivery_unavailable";
              }

              const legacyResult = await deliveryScope.run("legacy_child", () =>
                handler(event, ctx),
              );
              const result = outboundResult(event, legacyResult);
              api.logger.info(
                `${PLUGIN_ID}: v4.3 outbound enforced ${appendComparisonReason(
                  intent,
                  result,
                  `enforcement_fallback:${planned.fallbackReason || "unknown"}`,
                )}`,
              );
              return legacyResult;
            }

            const result = await deliveryScope.run("legacy_child", () =>
              handler(event, ctx),
            );
            if (config.unifiedOutboundShadowEnabled) {
              const comparison = compareOutboundResult(
                intent,
                outboundResult(event, result),
              );
              api.logger.info(
                `${PLUGIN_ID}: v4.3 outbound shadow ${formatOutboundComparison(comparison)}`,
              );
            }
            return result;
          };
          return api.on(name, wrapped, options);
        }

        return api.on(name, handler, options);
      },
    });

    (v4Plugin as any).register(wrappedApi);
    api.logger.info(
      `${PLUGIN_ID}: optional V4.3 shadow and enforced outbound coordinator registered`,
    );
  },
});
