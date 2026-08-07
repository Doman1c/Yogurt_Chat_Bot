import { CorrelationStore } from "./turn-correlation.ts";

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

  constructor(
    ttlMs = DEFAULT_TTL_MS,
    limit = DEFAULT_LIMIT,
    now: () => number = () => Date.now(),
  ) {
    this.store = new CorrelationStore<OutboundIntent>(ttlMs, limit, now);
  }

  register(aliases: string[], responsePlan: unknown): OutboundIntent | undefined {
    const intent = outboundIntentFromResponsePlan(responsePlan);
    return this.store.register(aliases, intent)?.value;
  }

  peek(aliases: string[]): OutboundIntent | undefined {
    return this.store.peek(aliases);
  }

  consume(aliases: string[]): OutboundIntent | undefined {
    return this.store.consume(aliases);
  }

  size(): number {
    return this.store.size();
  }
}

export const outboundTurns = new OutboundTurnStore();
