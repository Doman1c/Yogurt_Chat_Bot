import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import v21Plugin from "./index-v2.ts";
import {
  asRecord,
  contextAliases,
  optionalString,
  senderIdFromContext,
} from "./sticker-plugin-utils.ts";
import {
  addTurnCorrelationAliases,
  CorrelationStore,
} from "./turn-correlation.ts";
import { registerExtendedMemoryRuntime } from "./v3-memory-extended.ts";
import { registerV3Runtime } from "./v3-runtime.ts";

const PLUGIN_ID = "yogurt-social-injector";
const WEIXIN_CHANNEL = "openclaw-weixin";
const FINALIZER_CONTEXT_TTL_MS = 15 * 60_000;
const FINALIZER_CONTEXT_LIMIT = 256;
const basePlugin = v21Plugin as any;

type HookHandler = (event: any, ctx: any) => unknown | Promise<unknown>;

type FinalizerContext = {
  ctx: any;
};

function optionalText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function eventMetadata(event: any): Record<string, unknown> {
  return asRecord(event?.metadata);
}

function eventHasMedia(event: any): boolean {
  const metadata = eventMetadata(event);
  const payload = asRecord(event?.payload);
  return Boolean(
    (Array.isArray(event?.media) && event.media.length > 0) ||
      (Array.isArray(payload.media) && payload.media.length > 0) ||
      optionalText(event?.mediaUrl) ||
      (Array.isArray(event?.mediaUrls) && event.mediaUrls.length > 0) ||
      optionalText(payload.mediaUrl) ||
      (Array.isArray(payload.mediaUrls) && payload.mediaUrls.length > 0) ||
      optionalText(metadata.mediaUrl) ||
      (Array.isArray(metadata.mediaUrls) && metadata.mediaUrls.length > 0),
  );
}

function messageChannel(event: any, ctx: any): string {
  const metadata = eventMetadata(event);
  return (
    optionalText(event?.channel) ||
    optionalText(metadata.channel) ||
    optionalText(ctx?.channel) ||
    optionalText(ctx?.channelId) ||
    optionalText(ctx?.messageProvider) ||
    optionalText(ctx?.deliveryContext?.channel) ||
    "unknown"
  );
}

function messageTarget(event: any, ctx: any): string | undefined {
  return (
    optionalText(event?.to) ||
    optionalText(ctx?.to) ||
    optionalText(ctx?.chatId) ||
    optionalText(ctx?.conversationId) ||
    optionalText(ctx?.channelContext?.chat?.id) ||
    optionalText(ctx?.deliveryContext?.to) ||
    optionalText(ctx?.senderId)
  );
}

function messageAccountId(event: any, ctx: any): string | undefined {
  const metadata = eventMetadata(event);
  return (
    optionalText(event?.accountId) ||
    optionalText(metadata.accountId) ||
    optionalText(ctx?.accountId) ||
    optionalText(ctx?.channelContext?.accountId) ||
    optionalText(ctx?.deliveryContext?.accountId)
  );
}

function finalizerContextKeys(event: any, ctx: any): string[] {
  const channel = messageChannel(event, ctx);
  const senderId = senderIdFromContext(ctx);
  const target = messageTarget(event, ctx);
  const accountId = messageAccountId(event, ctx);
  const aliases = contextAliases(ctx, channel, senderId, event);
  if (target) {
    aliases.push(`peer:${channel}:${target}`);
    aliases.push(`value:${target}`);
    if (accountId) aliases.push(`account:${accountId}:${target}`);
  }
  return addTurnCorrelationAliases(aliases, event, ctx);
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Yogurt Social Injector",
  description:
    "Injects Yogurt social context, delivers bounded V2.1 stickers, and provides opt-in V3 memory and multi-bubble experiments.",
  register(api: any) {
    let stickerAgentEnd: HookHandler | undefined;
    const finalizerContexts = new CorrelationStore<FinalizerContext>(
      FINALIZER_CONTEXT_TTL_MS,
      FINALIZER_CONTEXT_LIMIT,
    );

    const baseApi = Object.create(api);
    Object.defineProperty(baseApi, "on", {
      configurable: true,
      enumerable: true,
      writable: false,
      value(name: string, handler: HookHandler, options?: unknown) {
        if (name === "agent_end") stickerAgentEnd = handler;
        if (name === "before_prompt_build") {
          const wrapped: HookHandler = async (event, ctx) => {
            const result = await handler(event, ctx);
            const prompt = optionalText(event?.prompt);
            const channel = messageChannel(event, ctx);
            const target = messageTarget(event, ctx);
            if (
              prompt &&
              (channel === WEIXIN_CHANNEL || Boolean(target?.endsWith("@im.wechat")))
            ) {
              finalizerContexts.register(finalizerContextKeys(event, ctx), { ctx });
            }
            return result;
          };
          return api.on(name, wrapped, options);
        }
        return api.on(name, handler, options);
      },
    });

    basePlugin.register(baseApi);
    api.logger.info(
      `${PLUGIN_ID}: V3 sticker finalizer captured=${stickerAgentEnd ? "true" : "false"}`,
    );

    api.on(
      "message_sending",
      async (event: any, ctx: any) => {
        if (!stickerAgentEnd || eventHasMedia(event)) return;
        const kind = optionalText(event?.kind) || optionalText(eventMetadata(event).kind);
        if (kind && kind !== "final") return;
        const text = optionalText(event?.content) || optionalText(event?.text);
        const target = messageTarget(event, ctx);
        if (!text || !target) return;
        const channel = messageChannel(event, ctx);
        if (channel !== WEIXIN_CHANNEL && !target.endsWith("@im.wechat")) return;
        const stored = finalizerContexts.consume(finalizerContextKeys(event, ctx));
        if (!stored) return;
        const accountId = messageAccountId(event, ctx) || messageAccountId({}, stored.ctx);
        const finalizerCtx = {
          ...ctx,
          ...stored.ctx,
          channel: WEIXIN_CHANNEL,
          channelId: WEIXIN_CHANNEL,
          chatId: target,
          ...(accountId ? { accountId } : {}),
        };

        api.logger.info(
          `${PLUGIN_ID}: pre-bubble sticker finalizer textChars=${text.length}`,
        );
        await stickerAgentEnd(
          {
            success: true,
            channel: WEIXIN_CHANNEL,
            content: text,
            messages: [{ role: "assistant", content: text }],
            to: target,
            runId: ctx?.runId || stored.ctx?.runId,
            messageId: ctx?.messageId || stored.ctx?.messageId,
            traceId: ctx?.traceId || stored.ctx?.traceId,
            ...(accountId ? { accountId } : {}),
          },
          finalizerCtx,
        );
      },
      { priority: 80, timeoutMs: 12000 },
    );

    registerV3Runtime(api);
    registerExtendedMemoryRuntime(api);
  },
});