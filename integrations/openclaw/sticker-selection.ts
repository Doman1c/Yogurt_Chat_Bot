import { randomUUID } from "node:crypto";

type StickerCandidate = {
  id: string;
  file: string;
  description: string;
};

type CandidateSnapshot = {
  id: string;
  aliases: string[];
  createdAt: number;
  expiresAt: number;
  candidates: Map<string, StickerCandidate>;
};

type PendingSelection = {
  snapshotId: string;
  sticker: StickerCandidate;
  selectedAt: number;
};

export type StickerSelectionResult =
  | {
      status: "selected";
      sticker: StickerCandidate;
      idempotent: boolean;
    }
  | {
      status: "rejected";
      reason:
        | "missing_context"
        | "snapshot_missing"
        | "snapshot_expired"
        | "not_in_candidates"
        | "already_selected";
    };

export class StickerSelectionStore {
  private readonly ttlMs: number;
  private readonly maxSnapshots: number;
  private readonly aliases = new Map<string, string>();
  private readonly snapshots = new Map<string, CandidateSnapshot>();
  private readonly pending = new Map<string, PendingSelection>();

  constructor({ ttlMs = 5 * 60_000, maxSnapshots = 256 } = {}) {
    this.ttlMs = Math.max(1_000, ttlMs);
    this.maxSnapshots = Math.max(8, maxSnapshots);
  }

  register(
    aliases: string[],
    candidates: StickerCandidate[],
    now = Date.now(),
  ): string | undefined {
    const normalizedAliases = uniqueNonEmpty(aliases);
    if (normalizedAliases.length === 0 || candidates.length === 0) {
      this.clear(normalizedAliases);
      return undefined;
    }

    this.prune(now);
    this.clear(normalizedAliases);

    const snapshotId = randomUUID();
    const snapshot: CandidateSnapshot = {
      id: snapshotId,
      aliases: normalizedAliases,
      createdAt: now,
      expiresAt: now + this.ttlMs,
      candidates: new Map(candidates.map((candidate) => [candidate.id, candidate])),
    };
    this.snapshots.set(snapshotId, snapshot);
    for (const alias of normalizedAliases) {
      this.aliases.set(alias, snapshotId);
    }
    this.trimToLimit();
    return snapshotId;
  }

  clear(aliases: string[]): void {
    for (const alias of uniqueNonEmpty(aliases)) {
      const snapshotId = this.aliases.get(alias);
      this.aliases.delete(alias);
      if (snapshotId) this.deleteSnapshotIfUnreferenced(snapshotId);
    }
  }

  select(
    aliases: string[],
    stickerId: string,
    now = Date.now(),
  ): StickerSelectionResult {
    const normalizedAliases = uniqueNonEmpty(aliases);
    if (normalizedAliases.length === 0) {
      return { status: "rejected", reason: "missing_context" };
    }

    this.prune(now);
    const snapshot = this.findSnapshot(normalizedAliases);
    if (!snapshot) {
      return { status: "rejected", reason: "snapshot_missing" };
    }
    if (snapshot.expiresAt <= now) {
      this.deleteSnapshot(snapshot.id);
      return { status: "rejected", reason: "snapshot_expired" };
    }

    const candidate = snapshot.candidates.get(stickerId);
    if (!candidate) {
      return { status: "rejected", reason: "not_in_candidates" };
    }

    const existing = this.pending.get(snapshot.id);
    if (existing) {
      if (existing.sticker.id === stickerId) {
        return { status: "selected", sticker: existing.sticker, idempotent: true };
      }
      return { status: "rejected", reason: "already_selected" };
    }

    this.pending.set(snapshot.id, {
      snapshotId: snapshot.id,
      sticker: candidate,
      selectedAt: now,
    });
    return { status: "selected", sticker: candidate, idempotent: false };
  }

  getPending(aliases: string[], now = Date.now()): PendingSelection | undefined {
    this.prune(now);
    const snapshot = this.findSnapshot(uniqueNonEmpty(aliases));
    return snapshot ? this.pending.get(snapshot.id) : undefined;
  }

  private findSnapshot(aliases: string[]): CandidateSnapshot | undefined {
    for (const alias of aliases) {
      const snapshotId = this.aliases.get(alias);
      if (!snapshotId) continue;
      const snapshot = this.snapshots.get(snapshotId);
      if (snapshot) return snapshot;
    }
    return undefined;
  }

  private prune(now: number): void {
    for (const snapshot of [...this.snapshots.values()]) {
      if (snapshot.expiresAt <= now) this.deleteSnapshot(snapshot.id);
    }
  }

  private trimToLimit(): void {
    if (this.snapshots.size <= this.maxSnapshots) return;
    const oldest = [...this.snapshots.values()].sort(
      (left, right) => left.createdAt - right.createdAt,
    );
    for (const snapshot of oldest.slice(0, this.snapshots.size - this.maxSnapshots)) {
      this.deleteSnapshot(snapshot.id);
    }
  }

  private deleteSnapshot(snapshotId: string): void {
    const snapshot = this.snapshots.get(snapshotId);
    if (!snapshot) return;
    this.snapshots.delete(snapshotId);
    this.pending.delete(snapshotId);
    for (const alias of snapshot.aliases) {
      if (this.aliases.get(alias) === snapshotId) this.aliases.delete(alias);
    }
  }

  private deleteSnapshotIfUnreferenced(snapshotId: string): void {
    for (const currentSnapshotId of this.aliases.values()) {
      if (currentSnapshotId === snapshotId) return;
    }
    this.deleteSnapshot(snapshotId);
  }
}

function uniqueNonEmpty(values: string[]): string[] {
  return [...new Set(values.filter((value) => value.trim().length > 0))];
}
