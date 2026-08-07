const DEFAULT_TTL_MS = 15 * 60_000;
const DEFAULT_LIMIT = 256;

export type CorrelatedEntry<T> = {
  id: number;
  aliases: string[];
  value: T;
  createdAt: number;
};

const STRONG_PREFIXES = ["run:", "message:", "trace:"] as const;

export function normalizeCorrelationAliases(aliases: string[]): string[] {
  return [...new Set(
    aliases
      .filter((value): value is string => typeof value === "string")
      .map((value) => value.trim())
      .filter(Boolean),
  )];
}

export function isStrongCorrelationAlias(alias: string): boolean {
  return STRONG_PREFIXES.some((prefix) => alias.startsWith(prefix));
}

export function addTurnCorrelationAliases(
  aliases: string[],
  event?: any,
  ctx?: any,
): string[] {
  const values = [...aliases];
  const add = (prefix: string, value: unknown) => {
    if (typeof value !== "string") return;
    const resolved = value.trim();
    if (resolved) values.push(`${prefix}:${resolved}`);
  };
  const metadata =
    event?.metadata && typeof event.metadata === "object" && !Array.isArray(event.metadata)
      ? event.metadata
      : {};
  const message =
    event?.message && typeof event.message === "object" && !Array.isArray(event.message)
      ? event.message
      : {};

  add("run", ctx?.runId);
  add("run", event?.runId);
  add("run", metadata.runId);
  add("message", ctx?.messageId);
  add("message", event?.messageId);
  add("message", message.id);
  add("message", metadata.messageId);
  add("trace", ctx?.traceId);
  add("trace", event?.traceId);
  add("trace", metadata.traceId);
  return normalizeCorrelationAliases(values);
}

export class CorrelationStore<T> {
  private readonly byAlias = new Map<string, Set<CorrelatedEntry<T>>>();
  private readonly byId = new Map<number, CorrelatedEntry<T>>();
  private nextId = 1;

  constructor(
    private readonly ttlMs = DEFAULT_TTL_MS,
    private readonly limit = DEFAULT_LIMIT,
    private readonly now: () => number = () => Date.now(),
  ) {}

  register(aliases: string[], value: T): CorrelatedEntry<T> | undefined {
    this.prune();
    const normalized = normalizeCorrelationAliases(aliases);
    if (normalized.length === 0) return undefined;
    const strong = normalized.filter(isStrongCorrelationAlias);

    if (strong.length > 0) {
      const duplicates = new Set<CorrelatedEntry<T>>();
      for (const alias of strong) {
        for (const entry of this.byAlias.get(alias) || []) duplicates.add(entry);
      }
      for (const entry of duplicates) this.remove(entry);
    } else {
      const existing = this.findEntry(normalized);
      if (existing) this.remove(existing);
    }

    const entry: CorrelatedEntry<T> = {
      id: this.nextId++,
      aliases: normalized,
      value,
      createdAt: this.now(),
    };
    for (const alias of normalized) {
      const bucket = this.byAlias.get(alias) || new Set<CorrelatedEntry<T>>();
      bucket.add(entry);
      this.byAlias.set(alias, bucket);
    }
    this.byId.set(entry.id, entry);
    if (this.byId.size > this.limit) this.prune(true);
    return entry;
  }

  findEntry(aliases: string[]): CorrelatedEntry<T> | undefined {
    this.prune();
    const normalized = normalizeCorrelationAliases(aliases);
    if (normalized.length === 0) return undefined;
    const strong = normalized.filter(isStrongCorrelationAlias);
    const searchAliases = strong.length > 0 ? strong : normalized;
    let newest: CorrelatedEntry<T> | undefined;
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
    return newest;
  }

  peek(aliases: string[]): T | undefined {
    return this.findEntry(aliases)?.value;
  }

  consumeEntry(aliases: string[]): CorrelatedEntry<T> | undefined {
    const entry = this.findEntry(aliases);
    if (!entry) return undefined;
    this.remove(entry);
    return entry;
  }

  consume(aliases: string[]): T | undefined {
    return this.consumeEntry(aliases)?.value;
  }

  getById(id: number): CorrelatedEntry<T> | undefined {
    this.prune();
    return this.byId.get(id);
  }

  remove(entry: CorrelatedEntry<T>): void {
    for (const alias of entry.aliases) {
      const bucket = this.byAlias.get(alias);
      if (!bucket) continue;
      bucket.delete(entry);
      if (bucket.size === 0) this.byAlias.delete(alias);
    }
    this.byId.delete(entry.id);
  }

  size(): number {
    this.prune();
    return this.byId.size;
  }

  clear(): void {
    this.byAlias.clear();
    this.byId.clear();
  }

  private prune(force = false): void {
    const cutoff = this.now() - this.ttlMs;
    const entries = [...this.byId.values()].sort(
      (left, right) => left.createdAt - right.createdAt || left.id - right.id,
    );
    for (const entry of entries) {
      if (!force && entry.createdAt >= cutoff) continue;
      this.remove(entry);
      if (force && this.byId.size <= Math.max(1, Math.floor(this.limit / 2))) break;
    }
  }
}
