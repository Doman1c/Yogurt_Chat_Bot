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
  type StickerOutcome,
} from "./outbound-coordinator.ts";
import { responsePlanTurns } from "./response-plan-store.ts";
import {
  onStickerLifecycle,
  type StickerLifecycleEvent,
} from "./sticker-lifecycle.ts";
import {
  asRecord,
  channelFromContext,
  contextAliases,
  optionalString,
  senderIdFromContext,
} from "./sticker-plugin-utils.ts";
import {
  addTurnCorrelationAliases,
  CorrelationStore,
  type CorrelatedEntry,
} from "./turn-correlation.ts";

const PLUGIN_ID = "yogurt-social-injector";
const WEIXIN_CHANNEL = "openclaw-weixin";
const CORRELATION_TTL_MS = 15 * 60_000;
const CORRELATION_LIMIT = 256;
const STICKER_FINALIZE_TIMEOUT_MS = 45_000;

type HookHandler = (event: any, ctx: any) => unknown | Promise<unknown>;
type DeliveryScope = "legacy_child" | "coordinator_child";
type OutboundMode = "shadow" | "enforced";

type V43Config = {
  unifiedOutboundShadowEnabled: boolean;
  unifiedOutboundEnforceEnabled: boolean;
  stickerEnabled: boolean;
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

type StickerSignal = {
  outcome: "selected" | "scheduled" | "sent" | "skipped" | "failed";
  reason: string;
  stickerId?: string;
};

type PendingOutbound = {
  intent: OutboundIntent;
  result: OutboundResult;
  mode: OutboundMode;
  comparisonReason: string;
  hostAction?: string;
  stickerId?: string;
  timer?: NodeJS.Timeout;
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
    stickerEnabled: booleanValue(raw.stickerEnabled, false),
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
  const payload = asRecord(event?.payload);
  return Boolean(
    (Array.isArray(event?.media) && event.media.length > 0) ||
      (Array.isArray(payload.media) && payload.media.length > 0) ||
      optionalString(event?.mediaUrl) ||
      (Array.isArray(event?.mediaUrls) && event.mediaUrls.length > 0) ||
      optionalString(payload.mediaUrl) ||
      (Array.isArray(payload.mediaUrls) && payload.mediaUrls.length > 0) ||
      optionalString(metadata.mediaUrl) ||
      (Array.isArray(metadata.mediaUrls) && metadata.mediaUrls.length > 0),
  );
}

function isFinalAssistantEvent(event: any): boolean {
  const metadata = eventMetadata(event);
  const kind = optionalString(event?.kind) || optionalString(metadata.kind);
  if (kind && kind !== "final") return false;
  const role = optionalString(event?.role) || optionalString(metadata.role);
  if (role && !["assistant", "ai", "bot"].includes(role.toLowerCase())) return false;
  return true;
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
  return addTurnCorrelationAliases(aliases, event, ctx);
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

function explicitPassthrough(text: string): Record<string, unknown> {
  return {
    content: text,
    cancel: false,
  };
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

  let adapter: any;
  try {
    adapter = await loadAdapter(WEIXIN_CHANNEL);
  } catch {
    return { fallbackReason: "adapter_load_failed" };
  }
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
    "Adds opt-in V4.3.1 shadow/enforced outbound coordination with structured turn and sticker lifecycle correlation.",
  register(api: any) {
    const deliveryScope = new AsyncLocalStorage<DeliveryScope>();
    const stickerSignals = new CorrelationStore<StickerSignal>(
      CORRELATION_TTL_MS,
      CORRELATION_LIMIT,
    );
    const pendingOutbounds = new CorrelationStore<PendingOutbound>(
      CORRELATION_TTL_MS,
      CORRELATION_LIMIT,
    );
    const wrappedApi = Object.create(api);

    const emitOutbound = (params: {
      mode: OutboundMode;
      intent: OutboundIntent;
      result: OutboundResult;
      comparisonReason: string;
      hostAction?: string;
    }) => {
      api.logger.info(
        `${PLUGIN_ID}: v4.3 outbound ${params.mode} ${appendComparisonReason(
          params.intent,
          params.result,
          params.comparisonReason,
        )}${params.hostAction ? ` hostAction=${params.hostAction}` : ""}`,
      );
    };

    const finalizePending = (
      pendingEntry: CorrelatedEntry<PendingOutbound>,
      signal: StickerSignal,
    ) => {
      if (signal.outcome === "selected" || signal.outcome === "scheduled") return;
      const pending = pendingEntry.value;
      if (
        pending.stickerId &&
        signal.stickerId &&
        pending.stickerId !== signal.stickerId
      ) {
        return;
      }
      if (pending.timer) clearTimeout(pending.timer);
      pendingOutbounds.remove(pendingEntry);
      const result: OutboundResult = {
        ...pending.result,
        stickerOutcome: signal.outcome,
        stickerReason: signal.reason,
      };
      api.logger.info(
        `${PLUGIN_ID}: v4.3 outbound finalized mode=${pending.mode} ${appendComparisonReason(
          pending.intent,
          result,
          pending.comparisonReason,
        )}${pending.hostAction ? ` hostAction=${pending.hostAction}` : ""} finalStage=sticker`,
      );
    };

    const recordStickerSignal = (event: StickerLifecycleEvent) => {
      const existingEntry = stickerSignals.findEntry(event.aliases);
      const existing = existingEntry?.value;
      const incomingTerminal = ["sent", "skipped", "failed"].includes(event.phase);
      const existingTerminal = existing
        ? ["sent", "skipped", "failed"].includes(existing.outcome)
        : false;
      if (existingTerminal && !incomingTerminal) return;
      if (existingEntry) stickerSignals.remove(existingEntry);

      const signal: StickerSignal = {
        outcome: event.phase,
        reason: event.reason,
        stickerId: event.stickerId || existing?.stickerId,
      };
      stickerSignals.register(event.aliases, signal);
      if (!incomingTerminal) return;
      const pendingEntry = pendingOutbounds.findEntry(event.aliases);
      if (pendingEntry) finalizePending(pendingEntry, signal);
    };

    onStickerLifecycle(recordStickerSignal);

    const recordOutbound = (params: {
      aliases: string[];
      config: V43Config;
      mode: OutboundMode;
      intent: OutboundIntent;
      result: OutboundResult;
      comparisonReason: string;
      hostAction?: string;
    }) => {
      const signal = params.config.stickerEnabled
        ? stickerSignals.peek(params.aliases)
        : undefined;
      let stickerOutcome: StickerOutcome;
      let stickerReason: string;
      let stickerId: string | undefined;

      if (!params.config.stickerEnabled) {
        stickerOutcome = "skipped";
        stickerReason = "sticker_disabled";
      } else if (!signal) {
        stickerOutcome = "skipped";
        stickerReason = "not_selected";
      } else if (signal.outcome === "selected" || signal.outcome === "scheduled") {
        stickerOutcome = "pending";
        stickerReason =
          signal.outcome === "scheduled" ? "delivery_scheduled" : signal.reason;
        stickerId = signal.stickerId;
      } else {
        stickerOutcome = signal.outcome;
        stickerReason = signal.reason;
        stickerId = signal.stickerId;
      }

      const result: OutboundResult = {
        ...params.result,
        stickerOutcome,
        stickerReason,
      };
      emitOutbound({
        mode: params.mode,
        intent: params.intent,
        result,
        comparisonReason: params.comparisonReason,
        hostAction: params.hostAction,
      });

      if (stickerOutcome !== "pending") return;
      const pending: PendingOutbound = {
        intent: params.intent,
        result,
        mode: params.mode,
        comparisonReason: params.comparisonReason,
        hostAction: params.hostAction,
        stickerId,
      };
      const pendingEntry = pendingOutbounds.register(params.aliases, pending);
      if (!pendingEntry) return;
      pending.timer = setTimeout(() => {
        const current = pendingOutbounds.getById(pendingEntry.id);
        if (!current) return;
        finalizePending(current, {
          outcome: "failed",
          reason: "delivery_outcome_timeout",
          stickerId: current.value.stickerId,
        });
      }, STICKER_FINALIZE_TIMEOUT_MS);
      pending.timer.unref?.();
    };

    Object.defineProperty(wrappedApi, "on", {
      configurable: true,
      enumerable: true,
      writable: false,
      value(name: string, handler: HookHandler, options?: unknown) {
        const priority = asRecord(options).priority;

        if (name === "before_prompt_build" && priority === 50) {
          const wrapped: HookHandler = async (event, ctx) => {
            const aliases = aliasesFor(event, ctx);
            const result = await handler(event, ctx);
            const structuredPlan = responsePlanTurns.consume(aliases);
            const config = readV43Config(api, event);
            if (!coordinatorEnabled(config)) return result;
            if (!structuredPlan) {
              api.logger.warn(
                `${PLUGIN_ID}: v4.3 outbound intent unavailable reason=structured_plan_missing`,
              );
              return result;
            }
            const intent = outboundTurns.register(aliases, structuredPlan);
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
            if (!isFinalAssistantEvent(event)) return handler(event, ctx);

            const aliases = aliasesFor(event, ctx);
            const intent = outboundTurns.consume(aliases);
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
                  recordOutbound({
                    aliases,
                    config,
                    mode: "enforced",
                    intent,
                    result,
                    comparisonReason: "enforced_target_applied",
                    hostAction: "explicit_passthrough",
                  });
                  return explicitPassthrough(text);
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
                  recordOutbound({
                    aliases,
                    config,
                    mode: "enforced",
                    intent,
                    result: direct.result,
                    comparisonReason: reason,
                    hostAction: "coordinator_delivery",
                  });
                  return direct.hookResult;
                }
                planned.fallbackReason =
                  direct.fallbackReason || "direct_delivery_unavailable";
              }

              const legacyResult = await deliveryScope.run("legacy_child", () =>
                handler(event, ctx),
              );
              const result = outboundResult(event, legacyResult);
              const useExplicitPassthrough = legacyResult === undefined;
              recordOutbound({
                aliases,
                config,
                mode: "enforced",
                intent,
                result,
                comparisonReason: `enforcement_fallback:${planned.fallbackReason || "unknown"}`,
                hostAction: useExplicitPassthrough
                  ? "explicit_passthrough"
                  : "legacy_result",
              });
              return useExplicitPassthrough
                ? explicitPassthrough(text)
                : legacyResult;
            }

            const result = await deliveryScope.run("legacy_child", () =>
              handler(event, ctx),
            );
            if (config.unifiedOutboundShadowEnabled) {
              recordOutbound({
                aliases,
                config,
                mode: "shadow",
                intent,
                result: outboundResult(event, result),
                comparisonReason: "shadow_observed",
              });
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
      `${PLUGIN_ID}: optional V4.3.1 shadow/enforced coordinator registered with structured turn and sticker lifecycle correlation`,
    );
  },
});