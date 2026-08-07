const DEFAULT_TTL_MS = 15 * 60_000;
const DEFAULT_LIMIT = 200;
const STRONG_ALIAS_PREFIXES = ["run:", "message:", "trace:"] as const;

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

type StoredIntent = {
  id: number;
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

function isStrongAlias(alias: string): boolean {
  return STRONG_ALIAS_PREFIXES.some((prefix) => alias.startsWith(prefix));
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
  private readonly byAlias = new Map<string, Set<StoredIntent>>();
  private readonly byId = new Map<number, StoredIntent>();
  private nextId = 1;
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
    const strong = normalized.filter(isStrongAlias);

    // A repeated registration with the same strong turn id replaces only that
    // turn. Shared session/peer aliases are allowed to point at multiple live
    // turns, so a newer turn cannot delete an older pending turn by accident.
    if (strong.length > 0) {
      const duplicates = new Set<StoredIntent>();
      for (const alias of strong) {
        for (const entry of this.byAlias.get(alias) || []) duplicates.add(entry);
      }
      for (const entry of duplicates) this.delete(entry);
    } else {
      const existing = this.find(normalized);
      if (existing) this.delete(existing);
    }

    const intent = outboundIntentFromResponsePlan(responsePlan);
    const entry: StoredIntent = {
      id: this.nextId++,
      aliases: normalized,
      intent,
      createdAt: this.now(),
    };
    for (const alias of normalized) {
      const bucket = this.byAlias.get(alias) || new Set<StoredIntent>();
      bucket.add(entry);
      this.byAlias.set(alias, bucket);
    }
    this.byId.set(entry.id, entry);
    if (this.byId.size > this.limit) this.prune(true);
    return intent;
  }

  peek(aliases: string[]): OutboundIntent | undefined {
    this.prune();
    return this.find(normalizeAliases(aliases))?.intent;
  }

  consume(aliases: string[]): OutboundIntent | undefined {
    this.prune();
    const entry = this.find(normalizeAliases(aliases));
    if (!entry) return undefined;
    this.delete(entry);
    return entry.intent;
  }

  size(): number {
    this.prune();
    return this.byId.size;
  }

  private find(aliases: string[]): StoredIntent | undefined {
    const strong = aliases.filter(isStrongAlias);
    const searchAliases = strong.length > 0 ? strong : aliases;
    let newest: StoredIntent | undefined;
    for (const alias of searchAliases) {
      for (const entry of this.byAlias.get(alias) || []) {
        if (
          !newest ||
          entry.createdAt > newest.createdAt ||
          (entry.createdAt === newest.createdAt && entry.id > newest.id)
        ) {
          newest = entry;
        }
      }
    }
    // If a caller supplies a strong id, never fall back to a weak session/peer
    // alias. A correlation miss is safer than consuming another turn's plan.
    return newest;
  }

  private delete(entry: StoredIntent): void {
    for (const alias of entry.aliases) {
      const bucket = this.byAlias.get(alias);
      if (!bucket) continue;
      bucket.delete(entry);
      if (bucket.size === 0) this.byAlias.delete(alias);
    }
    this.byId.delete(entry.id);
  }

  private prune(force = false): void {
    const cutoff = this.now() - this.ttlMs;
    const entries = [...this.byId.values()].sort(
      (left, right) => left.createdAt - right.createdAt || left.id - right.id,
    );
    for (const entry of entries) {
      if (!force && entry.createdAt >= cutoff) continue;
      this.delete(entry);
      if (force && this.byId.size <= Math.max(1, Math.floor(this.limit / 2))) break;
    }
  }
}

export const outboundTurns = new OutboundTurnStore();
