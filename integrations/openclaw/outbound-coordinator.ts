const DEFAULT_TTL_MS = 15 * 60_000;
const DEFAULT_LIMIT = 200;

export type OutboundIntent = {
  planVersion: string;
  bubbleTarget: number;
  stickerPreference: string;
  replyLength: string;
  stateVersion: number;
  source: "response_planner";
};

export type OutboundResult = {
  actualBubbleCount: number;
  textChars: number;
  outcome: "single" | "split" | "partial" | "fallback" | "media_passthrough";
  stickerOutcome: "unknown" | "none" | "selected" | "sent" | "failed";
};

export type OutboundComparison = {
  intent: OutboundIntent;
  result: OutboundResult;
  bubbleDelta: number;
  bubbleTargetMatched: boolean;
  reasons: string[];
};

type StoredIntent = {
  aliases: string[];
  intent: OutboundIntent;
  createdAt: number;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function textValue(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
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

function normalizeAliases(aliases: string[]): string[] {
  return [...new Set(
    aliases
      .filter((item): item is string => typeof item === "string")
      .map((item) => item.trim())
      .filter(Boolean),
  )];
}

export function outboundIntentFromResponsePlan(value: unknown): OutboundIntent {
  const plan = asRecord(value);
  return {
    planVersion: textValue(plan.plan_version, "4.1"),
    bubbleTarget: integerValue(plan.bubble_target, 1, 1, 3),
    stickerPreference: textValue(plan.sticker_preference, "neutral"),
    replyLength: textValue(plan.reply_length, "medium"),
    stateVersion: integerValue(plan.state_version, 0, 0, Number.MAX_SAFE_INTEGER),
    source: "response_planner",
  };
}

export function compareOutboundResult(
  intent: OutboundIntent,
  result: OutboundResult,
): OutboundComparison {
  const bubbleDelta = result.actualBubbleCount - intent.bubbleTarget;
  const reasons: string[] = [];
  if (bubbleDelta === 0) {
    reasons.push("bubble_target_matched");
  } else if (bubbleDelta > 0) {
    reasons.push("actual_bubbles_exceeded_target");
  } else {
    reasons.push("actual_bubbles_below_target");
  }
  if (result.outcome === "partial") reasons.push("partial_delivery");
  if (result.outcome === "fallback") reasons.push("delivery_fallback");
  if (result.outcome === "media_passthrough") reasons.push("media_passthrough");
  return {
    intent,
    result,
    bubbleDelta,
    bubbleTargetMatched: bubbleDelta === 0,
    reasons,
  };
}

export function formatOutboundComparison(comparison: OutboundComparison): string {
  const { intent, result } = comparison;
  return [
    `targetBubbles=${intent.bubbleTarget}`,
    `actualBubbles=${result.actualBubbleCount}`,
    `matched=${comparison.bubbleTargetMatched}`,
    `delta=${comparison.bubbleDelta}`,
    `outcome=${result.outcome}`,
    `sticker=${result.stickerOutcome}`,
    `textChars=${result.textChars}`,
    `stateVersion=${intent.stateVersion}`,
    `reasons=${comparison.reasons.join(",") || "none"}`,
  ].join(" ");
}

export class OutboundTurnStore {
  private readonly byAlias = new Map<string, StoredIntent>();
  private readonly ttlMs: number;
  private readonly limit: number;
  private readonly now: () => number;

  constructor(
    ttlMs = DEFAULT_TTL_MS,
    limit = DEFAULT_LIMIT,
    now: () => number = () => Date.now(),
  ) {
    this.ttlMs = ttlMs;
    this.limit = limit;
    this.now = now;
  }

  register(aliases: string[], responsePlan: unknown): OutboundIntent | undefined {
    this.prune();
    const normalized = normalizeAliases(aliases);
    if (normalized.length === 0) return undefined;
    const intent = outboundIntentFromResponsePlan(responsePlan);
    const entry: StoredIntent = {
      aliases: normalized,
      intent,
      createdAt: this.now(),
    };
    for (const alias of normalized) this.byAlias.set(alias, entry);
    if (this.byAlias.size > this.limit) this.prune(true);
    return intent;
  }

  peek(aliases: string[]): OutboundIntent | undefined {
    this.prune();
    for (const alias of normalizeAliases(aliases)) {
      const entry = this.byAlias.get(alias);
      if (entry) return entry.intent;
    }
    return undefined;
  }

  consume(aliases: string[]): OutboundIntent | undefined {
    this.prune();
    for (const alias of normalizeAliases(aliases)) {
      const entry = this.byAlias.get(alias);
      if (!entry) continue;
      this.delete(entry);
      return entry.intent;
    }
    return undefined;
  }

  size(): number {
    this.prune();
    return new Set(this.byAlias.values()).size;
  }

  private delete(entry: StoredIntent): void {
    for (const alias of entry.aliases) {
      if (this.byAlias.get(alias) === entry) this.byAlias.delete(alias);
    }
  }

  private prune(force = false): void {
    const cutoff = this.now() - this.ttlMs;
    const entries = [...new Set(this.byAlias.values())].sort(
      (left, right) => left.createdAt - right.createdAt,
    );
    for (const entry of entries) {
      if (!force && entry.createdAt >= cutoff) continue;
      this.delete(entry);
      if (force && this.byAlias.size <= Math.max(1, Math.floor(this.limit / 2))) {
        break;
      }
    }
  }
}

export const outboundTurns = new OutboundTurnStore();
