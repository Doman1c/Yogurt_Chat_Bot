import {
  CorrelationStore,
  isStrongCorrelationAlias,
  normalizeCorrelationAliases,
} from "./turn-correlation.ts";

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

export type StickerOutcome =
  | "unknown"
  | "pending"
  | "sent"
  | "skipped"
  | "failed";

export type OutboundResult = {
  actualBubbleCount: number;
  textChars: number;
  outcome: "single" | "split" | "partial" | "fallback" | "media_passthrough";
  stickerOutcome: StickerOutcome;
  stickerReason?: string;
};

export type OutboundComparison = {
  intent: OutboundIntent;
  result: OutboundResult;
  bubbleDelta: number;
  bubbleTargetMatched: boolean;
  reasons: string[];
};

export type OutboundCorrelationMatch =
  | "strict"
  | "weak_parent"
  | "cross_namespace_parent"
  | "miss";

export type OutboundConsumeResult = {
  intent?: OutboundIntent;
  match: OutboundCorrelationMatch;
  pendingBefore: number;
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
  if (result.stickerOutcome === "pending") reasons.push("sticker_pending");
  if (result.stickerOutcome === "skipped") reasons.push("sticker_skipped");
  if (result.stickerOutcome === "failed") reasons.push("sticker_failed");
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
    `stickerReason=${result.stickerReason || "none"}`,
    `textChars=${result.textChars}`,
    `stateVersion=${intent.stateVersion}`,
    `reasons=${comparison.reasons.join(",") || "none"}`,
  ].join(" ");
}

export class OutboundTurnStore {
  private readonly store: CorrelationStore<OutboundIntent>;
  private readonly blockedWeakFallbackEntryIds = new Set<number>();
  private readonly limit: number;

  constructor(
    ttlMs = DEFAULT_TTL_MS,
    limit = DEFAULT_LIMIT,
    now: () => number = () => Date.now(),
  ) {
    this.limit = limit;
    this.store = new CorrelationStore<OutboundIntent>(ttlMs, limit, now);
  }

  register(aliases: string[], responsePlan: unknown): OutboundIntent | undefined {
    this.pruneBlocked();
    const intent = outboundIntentFromResponsePlan(responsePlan);
    return this.store.register(aliases, intent)?.value;
  }

  peek(aliases: string[]): OutboundIntent | undefined {
    return this.store.peek(aliases);
  }

  peekParent(aliases: string[]): OutboundIntent | undefined {
    return this.store.peekParent(aliases);
  }

  consumeWithMatch(aliases: string[]): OutboundConsumeResult {
    this.pruneBlocked();
    const pendingBefore = this.store.size();
    const normalized = normalizeCorrelationAliases(aliases);
    const strong = normalized.filter(isStrongCorrelationAlias);
    const weak = normalized.filter((alias) => !isStrongCorrelationAlias(alias));

    const strictEntry = this.store.consumeEntry(normalized);
    if (strictEntry) {
      this.blockedWeakFallbackEntryIds.delete(strictEntry.id);
      return { intent: strictEntry.value, match: "strict", pendingBefore };
    }

    if (strong.length > 0) {
      // Generic correlation stays conservative. A same-namespace mismatch or a
      // strong callback that cannot be resolved through shared weak aliases is
      // not guessed here. If a weakly identifiable candidate exists, block it
      // from a later recursive weak-only child.
      const candidate = this.store.findParentEntry(weak);
      if (candidate) this.blockedWeakFallbackEntryIds.add(candidate.id);
      this.pruneBlocked();
      return { match: "miss", pendingBefore };
    }

    const parentEntry = this.store.findParentEntry(normalized);
    if (!parentEntry || this.blockedWeakFallbackEntryIds.has(parentEntry.id)) {
      return { match: "miss", pendingBefore };
    }
    this.store.remove(parentEntry);
    return { intent: parentEntry.value, match: "weak_parent", pendingBefore };
  }

  consumeTopLevelWithMatch(aliases: string[]): OutboundConsumeResult {
    const result = this.consumeWithMatch(aliases);
    if (result.intent) return result;

    const normalized = normalizeCorrelationAliases(aliases);
    const strong = normalized.filter(isStrongCorrelationAlias);
    if (strong.length === 0) return result;

    // index-v43 calls consume() only after its AsyncLocalStorage recursive-child
    // guard has established that this is the top-level parent send. At that
    // boundary we may recover one unique trace/message -> run parent even when
    // OpenClaw changed every weak alias between hooks. CorrelationStore still
    // requires disjoint strong namespaces, exactly one pending entry, and no
    // same-namespace disagreement.
    const parentEntry = this.store.consumeParentEntry(normalized);
    if (!parentEntry) return result;
    this.blockedWeakFallbackEntryIds.delete(parentEntry.id);
    return {
      intent: parentEntry.value,
      match: "cross_namespace_parent",
      pendingBefore: result.pendingBefore,
    };
  }

  consume(aliases: string[]): OutboundIntent | undefined {
    return this.consumeTopLevelWithMatch(aliases).intent;
  }

  size(): number {
    this.pruneBlocked();
    return this.store.size();
  }

  private pruneBlocked(): void {
    for (const id of [...this.blockedWeakFallbackEntryIds]) {
      if (!this.store.getById(id)) this.blockedWeakFallbackEntryIds.delete(id);
    }
    if (this.blockedWeakFallbackEntryIds.size <= this.limit) return;
    for (const id of [...this.blockedWeakFallbackEntryIds].slice(0, this.limit)) {
      this.blockedWeakFallbackEntryIds.delete(id);
    }
  }
}

export const outboundTurns = new OutboundTurnStore();
