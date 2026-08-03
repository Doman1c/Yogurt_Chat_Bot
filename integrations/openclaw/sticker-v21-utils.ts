import { asRecord, optionalString } from "./sticker-plugin-utils.ts";

export type StickerBridgeDecision = {
  shouldSend: boolean;
  mode: string;
  score: number;
  threshold: number;
  intents: string[];
  reasons: string[];
  deferred: boolean;
};

function finiteNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}

export function parseStickerBridgeDecision(value: unknown): StickerBridgeDecision {
  const raw = asRecord(value);
  return {
    shouldSend: raw.should_send === true,
    mode: optionalString(raw.mode) || "unknown",
    score: finiteNumber(raw.score),
    threshold: finiteNumber(raw.threshold),
    intents: stringArray(raw.intents).slice(0, 10),
    reasons: stringArray(raw.reasons).slice(0, 20),
    deferred: raw.deferred === true,
  };
}

export function assistantTextFromEvent(event: any, ctx?: any): string | undefined {
  const direct = [
    event?.content,
    event?.text,
    event?.output,
    event?.response,
    event?.finalResponse,
    event?.message?.content,
    event?.result?.content,
    event?.payload?.text,
    event?.payload?.content,
    ctx?.result?.content,
    ctx?.response?.content,
  ];
  for (const value of direct) {
    const resolved = optionalString(value);
    if (resolved) return resolved;
  }

  for (const container of [event?.messages, event?.outputMessages, ctx?.messages]) {
    if (!Array.isArray(container)) continue;
    for (const item of [...container].reverse()) {
      const role = optionalString(item?.role) || optionalString(item?.type);
      if (role && !["assistant", "ai", "bot", "output_text"].includes(role.toLowerCase())) {
        continue;
      }
      const resolved =
        optionalString(item?.content) ||
        optionalString(item?.text) ||
        optionalString(item?.message);
      if (resolved) return resolved;
    }
  }
  return undefined;
}

export function compactDecisionLog(decision: StickerBridgeDecision): string {
  const reasons = decision.reasons.join("|").replace(/\s+/g, "_").slice(0, 500) || "none";
  const intents = decision.intents.join("|").replace(/\s+/g, "_").slice(0, 200) || "none";
  return `mode=${decision.mode} shouldSend=${decision.shouldSend} score=${decision.score.toFixed(2)} threshold=${decision.threshold.toFixed(2)} intents=${intents} reasons=${reasons}`;
}
