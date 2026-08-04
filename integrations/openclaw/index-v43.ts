import { AsyncLocalStorage } from "node:async_hooks";

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import v4Plugin from "./index-v4.ts";
import {
  compareOutboundResult,
  formatOutboundComparison,
  outboundTurns,
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
const RESPONSE_PLAN_MARKER = "[Yogurt Response Plan V4.1]";

type HookHandler = (event: any, ctx: any) => unknown | Promise<unknown>;

type V43Config = {
  unifiedOutboundShadowEnabled: boolean;
};

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
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
  };
}

function eventMetadata(event: any): Record<string, unknown> {
  return asRecord(event?.metadata);
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
  const channel =
    optionalString(event?.channel) ||
    optionalString(eventMetadata(event).channel) ||
    channelFromContext(ctx);
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
  if (result.cancel === true && cancelReason === "yogurt_multi_bubble_partial") {
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
  if (result.cancel === true && cancelReason === "yogurt_multi_bubble") {
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

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Yogurt Social Injector",
  description:
    "Adds opt-in V4.3 shadow outbound coordination on top of V4.0-V4.2 and the V3 delivery path.",
  register(api: any) {
    const deliveryScope = new AsyncLocalStorage<boolean>();
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
            if (!config.unifiedOutboundShadowEnabled) return result;
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
            if (deliveryScope.getStore() === true) {
              return handler(event, ctx);
            }
            return deliveryScope.run(true, async () => {
              const result = await handler(event, ctx);
              const config = readV43Config(api, event);
              if (!config.unifiedOutboundShadowEnabled) return result;
              const intent = outboundTurns.consume(aliasesFor(event, ctx));
              if (!intent) return result;
              const comparison = compareOutboundResult(
                intent,
                outboundResult(event, result),
              );
              api.logger.info(
                `${PLUGIN_ID}: v4.3 outbound shadow ${formatOutboundComparison(comparison)}`,
              );
              return result;
            });
          };
          return api.on(name, wrapped, options);
        }

        return api.on(name, handler, options);
      },
    });

    (v4Plugin as any).register(wrappedApi);
    api.logger.info(
      `${PLUGIN_ID}: optional V4.3 shadow outbound coordinator registered`,
    );
  },
});
