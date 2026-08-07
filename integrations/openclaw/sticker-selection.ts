import { randomUUID } from "node:crypto";

import {
  CorrelationStore,
  normalizeCorrelationAliases,
} from "./turn-correlation.ts";

export type StickerCandidate = {
  id: string;
  file: string;
  description: string;
  score?: number;
  scenes?: string[];
};

type CandidateSnapshot = {
  id: string;
  aliases: string[];
  createdAt: number;
  expiresAt: number;
  candidates: Map<string, StickerCandidate>;
  pending?: PendingStickerSelection;
};

export type StickerSelectionMode = "auto" | "tool";

export type PendingStickerSelection = {
  snapshotId: string;
  sticker: StickerCandidate;
  selectedAt: number;
  sessionKey?: string;
  policyKey: string;
  mode: StickerSelectionMode;
  explicit: boolean;
};

type SelectionContext = {
  sessionKey?: string;
  policyKey?: string;
  mode?: StickerSelectionMode;
  explicit?: boolean;
};

export type StickerSelectionResult =
  | {
      status: "selected";
      sticker: StickerCandidate;
      idempotent: boolean;
      pending: PendingStickerSelection;
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
  private readonly snapshots: CorrelationStore<CandidateSnapshot>;
  private readonly snapshotEntryIds = new Map<string, number>();

  constructor({ ttlMs = 5 * 60_000, maxSnapshots = 256 } = {}) {
    this.ttlMs = Math.max(1_000, ttlMs);
    this.snapshots = new CorrelationStore<CandidateSnapshot>(
      this.ttlMs,
      Math.max(8, maxSnapshots),
    );
  }

  register(
    aliases: string[],
    candidates: StickerCandidate[],
    now = Date.now(),
  ): string | undefined {
    const normalizedAliases = normalizeCorrelationAliases(aliases);
    if (normalizedAliases.length === 0 || candidates.length === 0) {
      this.clear(normalizedAliases);
      return undefined;
    }

    this.cleanupStaleIds();
    this.clear(normalizedAliases);
    const snapshot: CandidateSnapshot = {
      id: randomUUID(),
      aliases: normalizedAliases,
      createdAt: now,
      expiresAt: now + this.ttlMs,
      candidates: new Map(candidates.map((candidate) => [candidate.id, candidate])),
    };
    const entry = this.snapshots.register(normalizedAliases, snapshot);
    if (!entry) return undefined;
    this.snapshotEntryIds.set(snapshot.id, entry.id);
    this.cleanupStaleIds();
    return snapshot.id;
  }

  clear(aliases: string[]): void {
    const entry = this.snapshots.findEntry(aliases);
    if (!entry) return;
    this.snapshotEntryIds.delete(entry.value.id);
    this.snapshots.remove(entry);
  }

  select(
    aliases: string[],
    stickerId: string,
    now = Date.now(),
    context: SelectionContext = {},
  ): StickerSelectionResult {
    const normalizedAliases = normalizeCorrelationAliases(aliases);
    if (normalizedAliases.length === 0) {
      return { status: "rejected", reason: "missing_context" };
    }

    this.cleanupStaleIds();
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

    const existing = snapshot.pending;
    if (existing) {
      if (existing.sticker.id === stickerId) {
        return {
          status: "selected",
          sticker: existing.sticker,
          idempotent: true,
          pending: existing,
        };
      }
      return { status: "rejected", reason: "already_selected" };
    }

    const pending: PendingStickerSelection = {
      snapshotId: snapshot.id,
      sticker: candidate,
      selectedAt: now,
      sessionKey: context.sessionKey,
      policyKey: context.policyKey || preferredPolicyKey(normalizedAliases),
      mode: context.mode || "tool",
      explicit: context.explicit === true,
    };
    snapshot.pending = pending;
    return {
      status: "selected",
      sticker: candidate,
      idempotent: false,
      pending,
    };
  }

  getPending(aliases: string[], now = Date.now()): PendingStickerSelection | undefined {
    this.cleanupStaleIds();
    const snapshot = this.findSnapshot(normalizeCorrelationAliases(aliases));
    if (!snapshot) return undefined;
    if (snapshot.expiresAt <= now) {
      this.deleteSnapshot(snapshot.id);
      return undefined;
    }
    return snapshot.pending;
  }

  consumePendingBySnapshot(
    snapshotId: string,
    now = Date.now(),
  ): PendingStickerSelection | undefined {
    this.cleanupStaleIds();
    const entryId = this.snapshotEntryIds.get(snapshotId);
    if (!entryId) return undefined;
    const entry = this.snapshots.getById(entryId);
    if (!entry) {
      this.snapshotEntryIds.delete(snapshotId);
      return undefined;
    }
    const snapshot = entry.value;
    if (snapshot.expiresAt <= now) {
      this.deleteSnapshot(snapshotId);
      return undefined;
    }
    const pending = snapshot.pending;
    if (!pending) return undefined;
    this.snapshotEntryIds.delete(snapshotId);
    this.snapshots.remove(entry);
    return pending;
  }

  consumePending(
    aliases: string[],
    now = Date.now(),
  ): PendingStickerSelection | undefined {
    const snapshot = this.findSnapshot(normalizeCorrelationAliases(aliases));
    return snapshot ? this.consumePendingBySnapshot(snapshot.id, now) : undefined;
  }

  private findSnapshot(aliases: string[]): CandidateSnapshot | undefined {
    return this.snapshots.peek(aliases);
  }

  private deleteSnapshot(snapshotId: string): void {
    const entryId = this.snapshotEntryIds.get(snapshotId);
    this.snapshotEntryIds.delete(snapshotId);
    if (!entryId) return;
    const entry = this.snapshots.getById(entryId);
    if (entry) this.snapshots.remove(entry);
  }

  private cleanupStaleIds(): void {
    for (const [snapshotId, entryId] of this.snapshotEntryIds) {
      if (!this.snapshots.getById(entryId)) this.snapshotEntryIds.delete(snapshotId);
    }
  }
}

export type StickerAutoPolicyConfig = {
  explicitMinScore: number;
  proactiveMinScore: number;
  proactiveMinMargin: number;
  globalCooldownSeconds: number;
  sceneCooldownSeconds: number;
  stickerCooldownSeconds: number;
  maxStickersPerHour: number;
};

export type StickerAutoDecision =
  | { selected: StickerCandidate; reason: "explicit" | "proactive" }
  | {
      selected?: undefined;
      reason:
        | "no_candidates"
        | "explicit_threshold"
        | "hourly_limit"
        | "global_cooldown"
        | "candidate_cooldown"
        | "proactive_threshold"
        | "score_margin";
    };

type ConversationHistory = {
  sentAt: number[];
  lastGlobalAt?: number;
  lastBySticker: Map<string, number>;
  lastByScene: Map<string, number>;
};

export class StickerAutoPolicyStore {
  private readonly histories = new Map<string, ConversationHistory>();

  choose(
    policyKey: string,
    candidates: StickerCandidate[],
    explicit: boolean,
    config: StickerAutoPolicyConfig,
    now = Date.now(),
  ): StickerAutoDecision {
    const sorted = [...candidates]
      .filter((candidate) => Number.isFinite(candidate.score))
      .sort(
        (left, right) =>
          (right.score || 0) - (left.score || 0) || left.id.localeCompare(right.id),
      );
    if (sorted.length === 0) return { reason: "no_candidates" };

    if (explicit) {
      const selected = sorted[0];
      return (selected.score || 0) >= config.explicitMinScore
        ? { selected, reason: "explicit" }
        : { reason: "explicit_threshold" };
    }

    const history = this.history(policyKey, now);
    if (config.maxStickersPerHour <= 0 || history.sentAt.length >= config.maxStickersPerHour) {
      return { reason: "hourly_limit" };
    }
    if (
      history.lastGlobalAt !== undefined &&
      now - history.lastGlobalAt < config.globalCooldownSeconds * 1000
    ) {
      return { reason: "global_cooldown" };
    }

    const eligible = sorted.filter((candidate) =>
      this.candidateIsEligible(candidate, history, config, now),
    );
    if (eligible.length === 0) return { reason: "candidate_cooldown" };

    const selected = eligible[0];
    if ((selected.score || 0) < config.proactiveMinScore) {
      return { reason: "proactive_threshold" };
    }
    const runnerUp = eligible[1];
    if (
      runnerUp &&
      (selected.score || 0) - (runnerUp.score || 0) < config.proactiveMinMargin
    ) {
      return { reason: "score_margin" };
    }
    return { selected, reason: "proactive" };
  }

  recordSent(policyKey: string, candidate: StickerCandidate, now = Date.now()): void {
    const history = this.history(policyKey, now);
    history.sentAt.push(now);
    history.lastGlobalAt = now;
    history.lastBySticker.set(candidate.id, now);
    for (const scene of candidate.scenes || []) {
      history.lastByScene.set(scene, now);
    }
  }

  private history(policyKey: string, now: number): ConversationHistory {
    let history = this.histories.get(policyKey);
    if (!history) {
      history = {
        sentAt: [],
        lastBySticker: new Map(),
        lastByScene: new Map(),
      };
      this.histories.set(policyKey, history);
    }
    history.sentAt = history.sentAt.filter((sentAt) => now - sentAt < 60 * 60_000);
    return history;
  }

  private candidateIsEligible(
    candidate: StickerCandidate,
    history: ConversationHistory,
    config: StickerAutoPolicyConfig,
    now: number,
  ): boolean {
    const lastStickerAt = history.lastBySticker.get(candidate.id);
    if (
      lastStickerAt !== undefined &&
      now - lastStickerAt < config.stickerCooldownSeconds * 1000
    ) {
      return false;
    }
    for (const scene of candidate.scenes || []) {
      const lastSceneAt = history.lastByScene.get(scene);
      if (
        lastSceneAt !== undefined &&
        now - lastSceneAt < config.sceneCooldownSeconds * 1000
      ) {
        return false;
      }
    }
    return true;
  }
}

function preferredPolicyKey(aliases: string[]): string {
  return (
    aliases.find((alias) => alias.startsWith("session:")) ||
    aliases.find((alias) => alias.startsWith("sender:")) ||
    aliases.find((alias) => alias.startsWith("conversation:")) ||
    aliases[0] ||
    "unknown"
  );
}
