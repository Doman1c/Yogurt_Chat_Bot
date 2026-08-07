import { CorrelationStore } from "./turn-correlation.ts";

export type ResponsePlanSnapshot = {
  plan_version: string;
  dialogue_act: string;
  primary_goal: string;
  reply_length: string;
  bubble_target: number;
  ask_followup: boolean;
  sticker_preference: string;
  state_version: number;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, 160) : fallback;
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

export function responsePlanSnapshot(value: unknown): ResponsePlanSnapshot | undefined {
  const plan = asRecord(value);
  if (Object.keys(plan).length === 0) return undefined;
  return {
    plan_version: stringValue(plan.plan_version, "4.1"),
    dialogue_act: stringValue(plan.dialogue_act, "respond"),
    primary_goal: stringValue(plan.primary_goal, "respond_to_latest_message"),
    reply_length: stringValue(plan.reply_length, "medium"),
    bubble_target: integerValue(plan.bubble_target, 1, 1, 3),
    ask_followup: plan.ask_followup === true,
    sticker_preference: stringValue(plan.sticker_preference, "neutral"),
    state_version: integerValue(plan.state_version, 0, 0, Number.MAX_SAFE_INTEGER),
  };
}

export class ResponsePlanStore {
  private readonly turns = new CorrelationStore<ResponsePlanSnapshot>(15 * 60_000, 256);

  register(aliases: string[], value: unknown): ResponsePlanSnapshot | undefined {
    const snapshot = responsePlanSnapshot(value);
    if (!snapshot) return undefined;
    return this.turns.register(aliases, snapshot)?.value;
  }

  peek(aliases: string[]): ResponsePlanSnapshot | undefined {
    return this.turns.peek(aliases);
  }

  consume(aliases: string[]): ResponsePlanSnapshot | undefined {
    return this.turns.consume(aliases);
  }
}

export const responsePlanTurns = new ResponsePlanStore();
