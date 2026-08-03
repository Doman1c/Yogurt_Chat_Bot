import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import v21Plugin from "./index-v2.ts";
import { registerExtendedMemoryRuntime } from "./v3-memory-extended.ts";
import { registerV3Runtime } from "./v3-runtime.ts";

const PLUGIN_ID = "yogurt-social-injector";
const WEIXIN_CHANNEL = "openclaw-weixin";
const basePlugin = v21Plugin as any;

type HookHandler = (event: any, ctx: any) => unknown | Promise<unknown>;

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

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Yogurt Social Injector",
  description:
    "Injects Yogurt social context, delivers bounded V2.1 stickers, and provides opt-in V3 memory and multi-bubble experiments.",
  register(api: any) {
    let stickerAgentEnd: HookHandler | undefined;

    // A plain wrapper is used instead of Proxy here. Some OpenClaw runtime API
    // implementations expose bound methods/accessors whose behaviour can differ
    // when read through a Proxy receiver. Keeping the original API as prototype
    // preserves every runtime property while overriding only typed-hook capture.
    const baseApi = Object.create(api);
    Object.defineProperty(baseApi, "on", {
      configurable: true,
      enumerable: true,
      writable: false,
      value(name: string, handler: HookHandler, options?: unknown) {
        if (name === "agent_end") stickerAgentEnd = handler;
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
    // Run the captured V2.1 finalizer with the complete reply first, then let the
    // lower-priority V3 hook split and cancel the original message.
    api.on(
      "message_sending",
      async (event: any, ctx: any) => {
        if (!stickerAgentEnd) return;
        if (eventHasMedia(event)) return;
        const text = optionalText(event?.content) || optionalText(event?.text);
        const target = messageTarget(event, ctx);
        if (!text || !target) return;
        const channel = messageChannel(event, ctx);
        if (channel !== WEIXIN_CHANNEL && !target.endsWith("@im.wechat")) return;
        const accountId = messageAccountId(event, ctx);

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
          {
            ...ctx,
            channel: WEIXIN_CHANNEL,
            channelId: WEIXIN_CHANNEL,
            chatId: target,
            ...(accountId ? { accountId } : {}),
          },
        );
      },
      { priority: 80, timeoutMs: 12000 },
    );

    registerV3Runtime(api);
    registerExtendedMemoryRuntime(api);
  },
});