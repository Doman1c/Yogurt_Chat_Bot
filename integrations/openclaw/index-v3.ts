import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import v21Plugin from "./index-v2.ts";
import { registerExtendedMemoryRuntime } from "./v3-memory-extended.ts";
import { registerV3Runtime } from "./v3-runtime.ts";

const PLUGIN_ID = "yogurt-social-injector";
const WEIXIN_CHANNEL = "openclaw-weixin";
const FINALIZER_CONTEXT_TTL_MS = 15 * 60_000;
const FINALIZER_CONTEXT_LIMIT = 256;
const basePlugin = v21Plugin as any;

type HookHandler = (event: any, ctx: any) => unknown | Promise<unknown>;

type FinalizerContext = {
  keys: string[];
  ctx: any;
  createdAt: number;
};

function optionalText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function eventMetadata(event: any): Record<string, unknown> {
  return event?.metadata && typeof event.metadata === "object" && !Array.isArray(event.metadata)
    ? event.metadata
    : {};
}

function eventHasMedia(event: any): boolean {
  const metadata = eventMetadata(event);
  return Boolean(
    optionalText(event?.mediaUrl) ||
      (Array.isArray(event?.mediaUrls) && event.mediaUrls.length > 0) ||
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
  const accountId = messageAccountId(event, ctx);
  const values = [
    event?.to,
    event?.conversationId,
    event?.sessionKey,
    ctx?.senderId,
    ctx?.to,
    ctx?.chatId,
    ctx?.conversationId,
    ctx?.sessionKey,
    ctx?.nativeChannelId,
    ctx?.channelContext?.chat?.id,
    ctx?.deliveryContext?.to,
    ctx?.deliveryContext?.conversationId,
    ctx?.deliveryContext?.chatId,
  ]
    .map(optionalText)
    .filter((value): value is string => Boolean(value));
  const keys: string[] = [];
  for (const value of [...new Set(values)]) {
    keys.push(`value:${value}`);
    if (accountId) keys.push(`account:${accountId}:${value}`);
  }
  return [...new Set(keys)];
}

class FinalizerContextStore {
  private readonly byKey = new Map<string, FinalizerContext>();

  register(event: any, ctx: any): void {
    this.prune();
    const keys = finalizerContextKeys(event, ctx);
    if (keys.length === 0) return;
    const entry: FinalizerContext = {
      keys,
      ctx,
      createdAt: Date.now(),
    };
    for (const key of keys) this.byKey.set(key, entry);
    if (this.byKey.size > FINALIZER_CONTEXT_LIMIT) this.prune(true);
  }

  consume(event: any, ctx: any): FinalizerContext | undefined {
    this.prune();
    for (const key of finalizerContextKeys(event, ctx)) {
      const entry = this.byKey.get(key);
      if (!entry) continue;
      this.delete(entry);
      return entry;
    }
    return undefined;
  }

  private delete(entry: FinalizerContext): void {
    for (const key of entry.keys) {
      if (this.byKey.get(key) === entry) this.byKey.delete(key);
    }
  }

  private prune(force = false): void {
    const cutoff = Date.now() - FINALIZER_CONTEXT_TTL_MS;
    const entries = [...new Set(this.byKey.values())].sort(
      (left, right) => left.createdAt - right.createdAt,
    );
    for (const entry of entries) {
      if (!force && entry.createdAt >= cutoff) continue;
      this.delete(entry);
      if (force && this.byKey.size <= FINALIZER_CONTEXT_LIMIT / 2) break;
    }
  }
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Yogurt Social Injector",
  description:
    "Injects Yogurt social context, delivers bounded V2.1 stickers, and provides opt-in V3 memory and multi-bubble experiments.",
  register(api: any) {
    let stickerAgentEnd: HookHandler | undefined;
    const finalizerContexts = new FinalizerContextStore();

    // Keep the original API as prototype and override only typed-hook capture.
    // The wrapped before_prompt_build stores the exact inbound context used by
    // V2.1, so the later message_sending finalizer can reuse identical aliases.
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
              finalizerContexts.register(event, ctx);
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

    // V2.1 normally finalizes WeChat sticker selection in agent_end/message_sent.
    // Multi-bubble delivery cancels the original outbound message before message_sent,
    // and this channel does not reliably expose final assistant text in agent_end.
    // Reuse the exact inbound context once for the complete reply, then remove it;
    // recursively sent child bubbles therefore skip this finalizer automatically.
    api.on(
      "message_sending",
      async (event: any, ctx: any) => {
        if (!stickerAgentEnd || eventHasMedia(event)) return;
        const text = optionalText(event?.content) || optionalText(event?.text);
        const target = messageTarget(event, ctx);
        if (!text || !target) return;
        const channel = messageChannel(event, ctx);
        if (channel !== WEIXIN_CHANNEL && !target.endsWith("@im.wechat")) return;
        const stored = finalizerContexts.consume(event, ctx);
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
