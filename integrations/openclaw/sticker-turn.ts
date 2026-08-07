import { randomUUID } from "node:crypto";

import {
  CorrelationStore,
  normalizeCorrelationAliases,
} from "./turn-correlation.ts";

export type StickerTurn = {
  id: string;
  aliases: string[];
  userId: string;
  message: string;
  explicit: boolean;
  forbidden: boolean;
  policyKey: string;
  sessionKey?: string;
  createdAt: number;
  expiresAt: number;
};

export class StickerTurnStore {
  private readonly ttlMs: number;
  private readonly turns: CorrelationStore<StickerTurn>;

  constructor({ ttlMs = 10 * 60_000, maxTurns = 256 } = {}) {
    this.ttlMs = Math.max(30_000, ttlMs);
    this.turns = new CorrelationStore<StickerTurn>(
      this.ttlMs,
      Math.max(8, maxTurns),
    );
  }

  register(
    aliases: string[],
    input: Omit<StickerTurn, "id" | "aliases" | "createdAt" | "expiresAt">,
    now = Date.now(),
  ): StickerTurn | undefined {
    const normalized = normalizeCorrelationAliases(aliases);
    if (normalized.length === 0) return undefined;
    const turn: StickerTurn = {
      ...input,
      id: randomUUID(),
      aliases: normalized,
      createdAt: now,
      expiresAt: now + this.ttlMs,
    };
    return this.turns.register(normalized, turn)?.value;
  }

  get(aliases: string[], now = Date.now()): StickerTurn | undefined {
    const turn = this.turns.peek(aliases);
    if (!turn) return undefined;
    if (turn.expiresAt <= now) {
      this.clear(turn.aliases);
      return undefined;
    }
    return turn;
  }

  consume(aliases: string[], now = Date.now()): StickerTurn | undefined {
    const turn = this.get(aliases, now);
    if (!turn) return undefined;
    const entry = this.turns.findEntry(turn.aliases);
    if (entry) this.turns.remove(entry);
    return turn;
  }

  clear(aliases: string[]): void {
    const entry = this.turns.findEntry(aliases);
    if (entry) this.turns.remove(entry);
  }
}
