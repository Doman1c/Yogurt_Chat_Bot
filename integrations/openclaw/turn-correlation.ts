const DEFAULT_TTL_MS = 15 * 60_000;
const DEFAULT_LIMIT = 256;

export type CorrelatedEntry<T> = {
  id: number;
  aliases: string[];
  value: T;
  createdAt: number;
};

const STRONG_PREFIXES = ["run:", "message:", "trace:"] as const;

type StrongPrefix = (typeof STRONG_PREFIXES)[number];

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

function strongPrefix(alias: string): StrongPrefix | undefined {
  return STRONG_PREFIXES.find((prefix) => alias.startsWith(prefix));
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

function newestEntry<T>(entries: Iterable<CorrelatedEntry<T>>): CorrelatedEntry<T> | undefined {
  let newest: CorrelatedEntry<T> | undefined;
  for (const entry of entries) {
    if (
      !newest ||
      entry.createdAt > newest.createdAt ||
      (entry.createdAt === newest.createdAt && entry.id > newest.id)
    ) {
      newest = entry;
    }
  }
  return newest;
}

function entryHasStrongAlias<T>(entry: CorrelatedEntry<T>): boolean {
  return entry.aliases.some(isStrongCorrelationAlias);
}

function strongAliasesByPrefix(aliases: string[]): Map<StrongPrefix, Set<string>> {
  const grouped = new Map<StrongPrefix, Set<string>>();
  for (const alias of aliases) {
    const prefix = strongPrefix(alias);
    if (!prefix) continue;
    const values = grouped.get(prefix) || new Set<string>();
    values.add(alias);
    grouped.set(prefix, values);
  }
  return grouped;
}

function hasSameNamespaceConflict(left: string[], right: string[]): boolean {
  const leftStrong = strongAliasesByPrefix(left);
  const rightStrong = strongAliasesByPrefix(right);
  for (const prefix of STRONG_PREFIXES) {
    const leftValues = leftStrong.get(prefix);
    const rightValues = rightStrong.get(prefix);
    if (!leftValues || !rightValues) continue;
    let intersects = false;
    for (const value of leftValues) {
      if (rightValues.has(value)) {
        intersects = true;
        break;
      }
    }
    if (!intersects) return true;
  }
  return false;
}

export class CorrelationStore<T> {
  private readonly byAlias = new Map<string, Set<CorrelatedEntry<T>>>();
  private readonly byId = new Map<number, CorrelatedEntry<T>>();
  private readonly ttlMs: number;
  private readonly limit: number;
  private readonly now: () => number;
  private nextId = 1;

  constructor(
    ttlMs = DEFAULT_TTL_MS,
    limit = DEFAULT_LIMIT,
    now: () => number = () => Date.now(),
  ) {
    this.ttlMs = ttlMs;
    this.limit = limit;
    this.now = now;
  }

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
    const weak = normalized.filter((alias) => !isStrongCorrelationAlias(alias));

    if (strong.length > 0) {
      const exact = new Set<CorrelatedEntry<T>>();
      for (const alias of strong) {
        for (const entry of this.byAlias.get(alias) || []) exact.add(entry);
      }
      const strongMatch = newestEntry(exact);
      if (strongMatch) return strongMatch;

      // OpenClaw may expose different strong-id namespaces at different hook
      // phases (for example traceId before prompt build, then runId during
      // delivery). Those are compatible unless the same namespace explicitly
      // disagrees. Use weak aliases only to locate a unique compatible parent.
      const compatible = new Set<CorrelatedEntry<T>>();
      for (const alias of weak) {
        for (const entry of this.byAlias.get(alias) || []) {
          if (!hasSameNamespaceConflict(normalized, entry.aliases)) {
            compatible.add(entry);
          }
        }
      }
      if (compatible.size === 1) return compatible.values().next().value;
      return undefined;
    }

    const candidates = new Set<CorrelatedEntry<T>>();
    for (const alias of weak) {
      for (const entry of this.byAlias.get(alias) || []) candidates.add(entry);
    }
    if (candidates.size !== 1) return undefined;
    const candidate = candidates.values().next().value as CorrelatedEntry<T>;

    // A weak-only child callback must never steal a parent Turn that already
    // has a run/message/trace identity. Hosts that omit all strong ids remain
    // supported when the stored Turn is also weak-only.
    return entryHasStrongAlias(candidate) ? undefined : candidate;
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
