export type StickerLifecyclePhase =
  | "selected"
  | "scheduled"
  | "sent"
  | "skipped"
  | "failed";

export type StickerLifecycleEvent = {
  aliases: string[];
  phase: StickerLifecyclePhase;
  reason: string;
  stickerId?: string;
  trigger?: string;
};

export type StickerLifecycleListener = (event: StickerLifecycleEvent) => void;

const listeners = new Set<StickerLifecycleListener>();

function normalizeAliases(values: string[]): string[] {
  return [...new Set(
    values
      .filter((value): value is string => typeof value === "string")
      .map((value) => value.trim())
      .filter(Boolean),
  )].slice(0, 32);
}

export function boundedLifecycleReason(value: unknown, fallback: string): string {
  const raw = typeof value === "string" ? value.trim().toLowerCase() : "";
  const normalized = raw
    .replace(/[^a-z0-9._:|=-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 160);
  return normalized || fallback;
}

export function emitStickerLifecycle(
  event: Omit<StickerLifecycleEvent, "aliases" | "reason"> & {
    aliases: string[];
    reason?: unknown;
  },
): void {
  const aliases = normalizeAliases(event.aliases);
  if (aliases.length === 0) return;
  const payload: StickerLifecycleEvent = {
    aliases,
    phase: event.phase,
    reason: boundedLifecycleReason(event.reason, event.phase),
    ...(typeof event.stickerId === "string" && event.stickerId.trim()
      ? { stickerId: event.stickerId.trim().slice(0, 80) }
      : {}),
    ...(typeof event.trigger === "string" && event.trigger.trim()
      ? { trigger: event.trigger.trim().slice(0, 80) }
      : {}),
  };
  for (const listener of [...listeners]) {
    try {
      listener(payload);
    } catch {
      // Lifecycle observation must never affect the chat or media send path.
    }
  }
}

export function onStickerLifecycle(listener: StickerLifecycleListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
