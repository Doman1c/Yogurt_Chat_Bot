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

type HookCorrelation = {
  aliases: string[];
};

type StickerSignal = {
  aliases: string[];
  outcome: "selected" | "sent" | "skipped" | "failed";
  reason: string;
  stickerId?: string;
  createdAt: number;
};

type PendingOutbound = {
  id: number;
  aliases: string[];
  intent: OutboundIntent;
  result: OutboundResult;
  mode: OutboundMode;
  comparisonReason: string;
  hostAction?: string;
  stickerId?: string;
  createdAt: number;
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
  return Boolean(
    optionalString(event?.mediaUrl) ||
      (Array.isArray(event?.mediaUrls) && event.mediaUrls.length > 0) ||
      optionalString(metadata.mediaUrl) ||
      (Array.isArray(metadata.mediaUrls) && metadata.mediaUrls.length > 0),
  );
}

function normalizeAliases(aliases: string[]): string[] {
  return [...new Set(
    aliases
      .filter((value): value is string => typeof value === "string")
      .map((value) => value.trim())
      .filter(Boolean),
  )];
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
  return normalizeAliases(aliases);
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

function explicitPassthrough(text: string): Record<string, unknown> {
  return {
    content: text,
    cancel: false,
  };
}

function boundedReason(value: unknown, fallback: string): string {
  const raw = typeof value === "string" ? value.trim().toLowerCase() : "";
  const normalized = raw
    .replace(/[^a-z0-9._:-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 120);
  return normalized || fallback;
}

function logField(message: string, name: string): string | undefined {
  const match = message.match(new RegExp(`${name}=([^\\s]+)`));
  return match?.[1];
}

function parentheticalReason(message: string, fallback: string): string {
  return boundedReason(message.match(/\(([^()]+)\)/)?.[1], fallback);
}

function parseStickerSignal(message: string): Omit<StickerSignal, "aliases" | "createdAt"> | undefined {
  if (!message.includes(`${PLUGIN_ID}:`)) return undefined;
  const stickerId = optionalString(logField(message, "sticker"));

  if (message.includes("sticker media sent")) {
    return { outcome: "sent", reason: "media_sent", ...(stickerId ? { stickerId } : {}) };
  }
  if (message.includes("sticker media attached")) {
    return { outcome: "sent", reason: "media_attached", ...(stickerId ? { stickerId } : {}) };
  }
  if (message.includes("sticker media send failed")) {
    return {
      outcome: "failed",
      reason: parentheticalReason(message, "media_send_failed"),
      ...(stickerId ? { stickerId } : {}),
    };
  }
  if (message.includes("sticker media attach failed")) {
    return {
      outcome: "failed",
      reason: parentheticalReason(message, "media_attach_failed"),
      ...(stickerId ? { stickerId } : {}),
    };
  }
  if (message.includes("sticker outbound delivery skipped")) {
    return {
      outcome: "failed",
      reason: parentheticalReason(message, "delivery_skipped"),
      ...(stickerId ? { stickerId } : {}),
    };
  }
  if (message.includes("post-reply sticker decision unavailable")) {
    return {
      outcome: "failed",
      reason: parentheticalReason(message, "decision_unavailable"),
      ...(stickerId ? { stickerId } : {}),
    };
  }
  if (message.includes("post-reply selection rejected")) {
    return {
      outcome: "failed",
      reason: boundedReason(logField(message, "reason"), "selection_rejected"),
      ...(stickerId ? { stickerId } : {}),
    };
  }
  if (message.includes("sticker outbound scheduled")) {
    return {
      outcome: "selected",
      reason: "delivery_scheduled",
      ...(stickerId ? { stickerId } : {}),
    };
  }
  if (message.includes("post-reply sticker selected")) {
    return {
      outcome: "selected",
      reason: boundedReason(logField(message, "reason"), "selected"),
      ...(stickerId ? { stickerId } : {}),
    };
  }
  if (message.includes("send decision rejected by delivery policy")) {
    return {
      outcome: "skipped",
      reason: boundedReason(logField(message, "reason"), "policy_rejected"),
    };
  }
  if (message.includes("send decision trigger=") && message.includes("shouldSend=false")) {
    const bridgeReason = logField(message, "reasons");
    return {
      outcome: "skipped",
      reason: boundedReason(
        bridgeReason && bridgeReason !== "none" ? bridgeReason : undefined,
        "bridge_no_send",
      ),
    };
  }
  if (message.includes("injection ready")) {
    const decision = logField(message, "stickerDecision");
    if (["user_forbid", "no_candidates", "disabled"].includes(decision || "")) {
      return {
        outcome: "skipped",
        reason: boundedReason(decision, "not_selected"),
      };
    }
  }
  if (message.includes("social layer unavailable")) {
    return {
      outcome: "failed",
      reason: parentheticalReason(message, "social_layer_unavailable"),
    };
  }
  return undefined;
}

class StickerSignalStore {
  private readonly byAlias = new Map<string, StickerSignal>();

  record(aliases: string[], signal: Omit<StickerSignal, "aliases" | "createdAt">): StickerSignal | undefined {
    this.prune();
    const normalized = normalizeAliases(aliases);
    if (normalized.length === 0) return undefined;
    const existing = this.peek(normalized);
    if (
      existing &&
      existing.outcome !== "selected" &&
      signal.outcome === "selected"
    ) {
      return existing;
    }
    if (existing) this.delete(existing);
    const entry: StickerSignal = {
      ...signal,
      aliases: normalized,
      stickerId: signal.stickerId || existing?.stickerId,
      createdAt: Date.now(),
    };
    for (const alias of normalized) this.byAlias.set(alias, entry);
    if (this.byAlias.size > CORRELATION_LIMIT) this.prune(true);
    return entry;
  }

  peek(aliases: string[]): StickerSignal | undefined {
    this.prune();
    for (const alias of normalizeAliases(aliases)) {
      const entry = this.byAlias.get(alias);
      if (entry) return entry;
    }
    return undefined;
  }

  clear(aliases: string[]): void {
    const entry = this.peek(aliases);
    if (entry) this.delete(entry);
  }

  private delete(entry: StickerSignal): void {
    for (const alias of entry.aliases) {
      if (this.byAlias.get(alias) === entry) this.byAlias.delete(alias);
    }
  }

  private prune(force = false): void {
    const cutoff = Date.now() - CORRELATION_TTL_MS;
    const entries = [...new Set(this.byAlias.values())].sort(
      (left, right) => left.createdAt - right.createdAt,
    );
    for (const entry of entries) {
      if (!force && entry.createdAt >= cutoff) continue;
      this.delete(entry);
      if (force && this.byAlias.size <= CORRELATION_LIMIT / 2) break;
    }
  }
}

class PendingOutboundStore {
  private readonly byAlias = new Map<string, PendingOutbound>();
  private readonly byId = new Map<number, PendingOutbound>();
  private nextId = 1;

  register(
    aliases: string[],
    value: Omit<PendingOutbound, "id" | "aliases" | "createdAt">,
  ): PendingOutbound | undefined {
    this.prune();
    const normalized = normalizeAliases(aliases);
    if (normalized.length === 0) return undefined;
    const existing = this.peek(normalized);
    if (existing) this.delete(existing);
    const entry: PendingOutbound = {
      ...value,
      id: this.nextId++,
      aliases: normalized,
      createdAt: Date.now(),
    };
    for (const alias of normalized) this.byAlias.set(alias, entry);
    this.byId.set(entry.id, entry);
    if (this.byAlias.size > CORRELATION_LIMIT) this.prune(true);
    return entry;
  }

  peek(aliases: string[]): PendingOutbound | undefined {
    this.prune();
    for (const alias of normalizeAliases(aliases)) {
      const entry = this.byAlias.get(alias);
      if (entry) return entry;
    }
    return undefined;
  }

  getById(id: number): PendingOutbound | undefined {
    this.prune();
    return this.byId.get(id);
  }

  remove(entry: PendingOutbound): void {
    this.delete(entry);
  }

  private delete(entry: PendingOutbound): void {
    if (entry.timer) clearTimeout(entry.timer);
    for (const alias of entry.aliases) {
      if (this.byAlias.get(alias) === entry) this.byAlias.delete(alias);
    }
    this.byId.delete(entry.id);
  }

  private prune(force = false): void {
    const cutoff = Date.now() - CORRELATION_TTL_MS;
    const entries = [...this.byId.values()].sort(
      (left, right) => left.createdAt - right.createdAt,
    );
    for (const entry of entries) {
      if (!force && entry.createdAt >= cutoff) continue;
      this.delete(entry);
      if (force && this.byAlias.size <= CORRELATION_LIMIT / 2) break;
    }
  }
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
    const hookCorrelation = new AsyncLocalStorage<HookCorrelation>();
    const stickerSignals = new StickerSignalStore();
    const pendingOutbounds = new PendingOutboundStore();
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
      pending: PendingOutbound,
      signal: Pick<StickerSignal, "outcome" | "reason" | "stickerId">,
    ) => {
      if (signal.outcome === "selected") return;
      if (
        pending.stickerId &&
        signal.stickerId &&
        pending.stickerId !== signal.stickerId
      ) {
        return;
      }
      const result: OutboundResult = {
        ...pending.result,
        stickerOutcome: signal.outcome,
        stickerReason: signal.reason,
      };
      pendingOutbounds.remove(pending);
      api.logger.info(
        `${PLUGIN_ID}: v4.3 outbound finalized mode=${pending.mode} ${appendComparisonReason(
          pending.intent,
          result,
          pending.comparisonReason,
        )}${pending.hostAction ? ` hostAction=${pending.hostAction}` : ""} finalStage=sticker`,
      );
    };

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
      } else if (signal.outcome === "selected") {
        stickerOutcome = "pending";
        stickerReason = signal.reason;
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
      const pending = pendingOutbounds.register(params.aliases, {
        intent: params.intent,
        result,
        mode: params.mode,
        comparisonReason: params.comparisonReason,
        hostAction: params.hostAction,
        stickerId,
      });
      if (!pending) return;
      pending.timer = setTimeout(() => {
        const current = pendingOutbounds.getById(pending.id);
        if (!current) return;
        finalizePending(current, {
          outcome: "failed",
          reason: "delivery_outcome_timeout",
          stickerId: current.stickerId,
        });
      }, STICKER_FINALIZE_TIMEOUT_MS);
      pending.timer.unref?.();
    };

    const captureNestedStickerLog = (value: unknown) => {
      const message = typeof value === "string" ? value : "";
      if (!message) return;
      const parsed = parseStickerSignal(message);
      if (!parsed) return;
      const scope = hookCorrelation.getStore();
      if (!scope || scope.aliases.length === 0) return;
      const signal = stickerSignals.record(scope.aliases, parsed);
      if (!signal || signal.outcome === "selected") return;
      const pending = pendingOutbounds.peek(scope.aliases);
      if (pending) finalizePending(pending, signal);
    };

    const nestedLogger = Object.create(api.logger || null);
    for (const level of ["info", "warn", "error"] as const) {
      const base = api.logger?.[level];
      if (typeof base !== "function") continue;
      Object.defineProperty(nestedLogger, level, {
        configurable: true,
        enumerable: true,
        writable: false,
        value(...args: unknown[]) {
          base.apply(api.logger, args);
          captureNestedStickerLog(args[0]);
        },
      });
    }
    Object.defineProperty(wrappedApi, "logger", {
      configurable: true,
      enumerable: true,
      writable: false,
      value: nestedLogger,
    });

    Object.defineProperty(wrappedApi, "on", {
      configurable: true,
      enumerable: true,
      writable: false,
      value(name: string, handler: HookHandler, options?: unknown) {
        const priority = asRecord(options).priority;

        if (name === "before_prompt_build" && priority === 50) {
          const wrapped: HookHandler = async (event, ctx) => {
            const aliases = aliasesFor(event, ctx);
            return hookCorrelation.run({ aliases }, async () => {
              const result = await handler(event, ctx);
              const config = readV43Config(api, event);
              if (!coordinatorEnabled(config)) return result;
              const plan = responsePlanFromHookResult(result);
              if (!plan) return result;
              const intent = outboundTurns.register(aliases, plan);
              if (intent) {
                api.logger.info(
                  `${PLUGIN_ID}: v4.3 outbound intent ready targetBubbles=${intent.bubbleTarget} replyLength=${intent.replyLength} sticker=${intent.stickerPreference} stateVersion=${intent.stateVersion}`,
                );
              }
              return result;
            });
          };
          return api.on(name, wrapped, options);
        }

        if (name === "message_sending" && priority === 70) {
          const wrapped: HookHandler = async (event, ctx) => {
            const aliases = aliasesFor(event, ctx);
            return hookCorrelation.run({ aliases }, async () => {
              const scope = deliveryScope.getStore();
              if (scope === "coordinator_child") return undefined;
              if (scope === "legacy_child") return handler(event, ctx);

              const config = readV43Config(api, event);
              if (!coordinatorEnabled(config)) return handler(event, ctx);
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
                  planned.fallbackReason = direct.fallbackReason || "direct_delivery_unavailable";
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
            });
          };
          return api.on(name, wrapped, options);
        }

        const scoped: HookHandler = async (event, ctx) => {
          const aliases = aliasesFor(event, ctx);
          if (name === "before_prompt_build" && priority === 60) {
            stickerSignals.clear(aliases);
          }
          return hookCorrelation.run({ aliases }, () => handler(event, ctx));
        };
        return api.on(name, scoped, options);
      },
    });

    (v4Plugin as any).register(wrappedApi);
    api.logger.info(
      `${PLUGIN_ID}: optional V4.3 shadow and enforced outbound coordinator registered`,
    );
  },
});
