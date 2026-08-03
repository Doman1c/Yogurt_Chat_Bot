import { randomUUID } from "node:crypto";

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
  private readonly maxTurns: number;
  private readonly aliases = new Map<string, string>();
  private readonly turns = new Map<string, StickerTurn>();

  constructor({ ttlMs = 10 * 60_000, maxTurns = 256 } = {}) {
    this.ttlMs = Math.max(30_000, ttlMs);
    this.maxTurns = Math.max(8, maxTurns);
  }

  register(
    aliases: string[],
    input: Omit<StickerTurn, "id" | "aliases" | "createdAt" | "expiresAt">,
    now = Date.now(),
  ): StickerTurn | undefined {
    const normalized = uniqueNonEmpty(aliases);
    if (normalized.length === 0) return undefined;
    this.prune(now);
    this.clear(normalized);
    const turn: StickerTurn = {
      ...input,
      id: randomUUID(),
      aliases: normalized,
      createdAt: now,
      expiresAt: now + this.ttlMs,
    };
    this.turns.set(turn.id, turn);
    for (const alias of normalized) this.aliases.set(alias, turn.id);
    this.trim();
    return turn;
  }

  get(aliases: string[], now = Date.now()): StickerTurn | undefined {
    this.prune(now);
    for (const alias of uniqueNonEmpty(aliases)) {
      const id = this.aliases.get(alias);
      if (!id) continue;
      const turn = this.turns.get(id);
      if (turn) return turn;
    }
    return undefined;
  }

  consume(aliases: string[], now = Date.now()): StickerTurn | undefined {
    const turn = this.get(aliases, now);
    if (!turn) return undefined;
    this.delete(turn.id);
    return turn;
  }

  clear(aliases: string[]): void {
    for (const alias of uniqueNonEmpty(aliases)) {
      const id = this.aliases.get(alias);
      this.aliases.delete(alias);
      if (id) this.deleteIfUnreferenced(id);
    }
  }

  private prune(now: number): void {
    for (const turn of [...this.turns.values()]) {
      if (turn.expiresAt <= now) this.delete(turn.id);
    }
  }

  private trim(): void {
    if (this.turns.size <= this.maxTurns) return;
    const oldest = [...this.turns.values()].sort((a, b) => a.createdAt - b.createdAt);
    for (const turn of oldest.slice(0, this.turns.size - this.maxTurns)) this.delete(turn.id);
  }

  private delete(id: string): void {
    const turn = this.turns.get(id);
    if (!turn) return;
    this.turns.delete(id);
    for (const alias of turn.aliases) {
      if (this.aliases.get(alias) === id) this.aliases.delete(alias);
    }
  }

  private deleteIfUnreferenced(id: string): void {
    for (const current of this.aliases.values()) {
      if (current === id) return;
    }
    this.delete(id);
  }
}

function uniqueNonEmpty(values: string[]): string[] {
  return [...new Set(values.filter((value) => value.trim().length > 0))];
}
