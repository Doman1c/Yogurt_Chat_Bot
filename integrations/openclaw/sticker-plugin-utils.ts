import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, delimiter, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import type {
  StickerAutoPolicyConfig,
  StickerCandidate,
  StickerSelectionMode,
} from "./sticker-selection.ts";

const DEFAULT_TIMEOUT_MS = 2500;
const MAX_BRIDGE_OUTPUT_BYTES = 1024 * 1024;
const STICKER_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$/;
const STICKER_FILE_PATTERN = /^images\/[A-Za-z0-9][A-Za-z0-9._/-]*\.(png|jpg|jpeg|webp|gif)$/i;
const STICKER_CONTEXT_MARKER = "[Yogurt Sticker Candidates]";

type UnknownSenderPolicy = "default" | "pseudonymous" | "skip";

export type PluginConfig = {
  enabled: boolean;
  pythonPath: string;
  repoPath: string;
  dbPath: string;
  defaultUserId: string;
  senderMap: Record<string, string>;
  ownerSenderIds: string[];
  unknownSenderPolicy: UnknownSenderPolicy;
  allowedChannels: string[];
  onlyUserTurns: boolean;
  timeoutMs: number;
  maxMemories: number;
  stickerEnabled: boolean;
  stickerConfigPath: string;
  stickerSelectionMode: StickerSelectionMode;
};

export type StickerDeliveryConfig = StickerAutoPolicyConfig & {
  deliveryDelayMs: number;
  maxBytes: number;
};

export type BridgeResult = {
  social_context?: unknown;
  sticker_candidates?: unknown;
};

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function stringValue(value: unknown, fallback: string): string {
  return optionalString(value) || fallback;
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
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

function numberValue(
  value: unknown,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(maximum, Math.max(minimum, value))
    : fallback;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

function expandHome(value: string): string {
  if (value === "~") return homedir();
  return value.startsWith("~/") ? join(homedir(), value.slice(2)) : value;
}

function expandPath(api: any, value: string): string {
  const fallback = expandHome(value);
  try {
    const resolved = api.resolvePath?.(value);
    return optionalString(resolved) || fallback;
  } catch {
    return fallback;
  }
}

export function readConfig(api: any, event?: any): PluginConfig {
  const raw = {
    ...asRecord(api.pluginConfig),
    ...asRecord(asRecord(event?.context).pluginConfig),
  };
  const inferredRepoPath = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const unknownSenderPolicy =
    raw.unknownSenderPolicy === "pseudonymous" || raw.unknownSenderPolicy === "skip"
      ? raw.unknownSenderPolicy
      : "default";
  const senderMap = Object.fromEntries(
    Object.entries(asRecord(raw.senderMap)).filter(
      (entry): entry is [string, string] => typeof entry[1] === "string" && !!entry[1].trim(),
    ),
  );
  return {
    enabled: booleanValue(raw.enabled, true),
    pythonPath: stringValue(raw.pythonPath, "python3"),
    repoPath: expandPath(api, stringValue(raw.repoPath, inferredRepoPath)),
    dbPath: expandPath(
      api,
      stringValue(
        raw.dbPath,
        process.env.YOGURT_SOCIAL_DB || "~/.openclaw/yogurt-social/memory.sqlite3",
      ),
    ),
    defaultUserId: stringValue(raw.defaultUserId, "owner"),
    senderMap,
    ownerSenderIds: stringArray(raw.ownerSenderIds),
    unknownSenderPolicy,
    allowedChannels: stringArray(raw.allowedChannels),
    onlyUserTurns: booleanValue(raw.onlyUserTurns, true),
    timeoutMs: integerValue(raw.timeoutMs, DEFAULT_TIMEOUT_MS, 250, 10000),
    maxMemories: integerValue(raw.maxMemories, 3, 0, 10),
    stickerEnabled: booleanValue(raw.stickerEnabled, false),
    stickerConfigPath: expandPath(
      api,
      stringValue(raw.stickerConfigPath, "social_layer/config/stickers.json"),
    ),
    stickerSelectionMode: raw.stickerSelectionMode === "tool" ? "tool" : "auto",
  };
}

export function readStickerDeliveryConfig(path: string): StickerDeliveryConfig {
  let raw: Record<string, unknown> = {};
  try {
    raw = asRecord(JSON.parse(readFileSync(path, "utf8")));
  } catch {
    // Python runtime loading remains authoritative; delivery uses safe defaults.
  }
  return {
    explicitMinScore: numberValue(raw.explicit_min_score, 5, 0, 100),
    proactiveMinScore: numberValue(raw.proactive_min_score, 7, 0, 100),
    proactiveMinMargin: numberValue(raw.proactive_min_margin, 2, 0, 100),
    globalCooldownSeconds: integerValue(raw.global_cooldown_seconds, 300, 0, 86400),
    sceneCooldownSeconds: integerValue(raw.scene_cooldown_seconds, 900, 0, 86400),
    stickerCooldownSeconds: integerValue(
      raw.default_sticker_cooldown_seconds,
      1800,
      0,
      604800,
    ),
    maxStickersPerHour: integerValue(raw.max_stickers_per_hour, 4, 0, 60),
    deliveryDelayMs: integerValue(raw.delivery_delay_ms, 1500, 0, 10000),
    maxBytes: integerValue(raw.delivery_max_bytes, 10 * 1024 * 1024, 1024, 50 * 1024 * 1024),
  };
}

export function senderIdFromContext(ctx: any): string | undefined {
  const delivery = asRecord(ctx?.deliveryContext);
  return (
    optionalString(ctx?.senderId) ||
    optionalString(ctx?.requesterSenderId) ||
    optionalString(ctx?.channelContext?.sender?.id) ||
    optionalString(ctx?.requester?.senderId) ||
    optionalString(delivery.requesterSenderId || delivery.senderId)
  );
}

export function sessionKeyFromContext(ctx: any): string | undefined {
  return optionalString(ctx?.sessionKey) || optionalString(asRecord(ctx?.deliveryContext).sessionKey);
}

/** Native Voice turns are local-only and must bypass social and Memory hooks. */
export function isNativeVoiceContext(ctx: any, event?: any): boolean {
  const candidates = [
    sessionKeyFromContext(ctx),
    optionalString(event?.sessionKey),
    optionalString(asRecord(event?.context).sessionKey),
    optionalString(asRecord(ctx?.deliveryContext).sessionKey),
  ];
  return candidates.some((value) => Boolean(value && value.startsWith("native:voice:")));
}

export function channelFromContext(ctx: any): string {
  const delivery = asRecord(ctx?.deliveryContext);
  return (
    optionalString(ctx?.channel) ||
    optionalString(ctx?.channelId) ||
    optionalString(ctx?.messageProvider) ||
    optionalString(ctx?.requester?.channel) ||
    optionalString(delivery.channel) ||
    optionalString(delivery.provider) ||
    "unknown"
  );
}

export function contextAliases(
  ctx: any,
  channel: string,
  senderId?: string,
  event?: any,
): string[] {
  const aliases: string[] = [];
  const add = (prefix: string, value: unknown) => {
    const resolved = optionalString(value);
    if (resolved) aliases.push(`${prefix}:${resolved}`);
  };
  add("run", ctx?.runId);
  add("run", event?.runId);
  add("session", ctx?.sessionKey);
  add("session", event?.sessionKey);
  add("session-id", ctx?.sessionId);
  add("native", ctx?.nativeChannelId);
  add("conversation", ctx?.conversationId);
  add("conversation", event?.conversationId);
  add("target", ctx?.to);
  add("target", ctx?.To);
  add("target", event?.to);
  const delivery = asRecord(ctx?.deliveryContext);
  add("native", delivery.nativeChannelId);
  add("conversation", delivery.conversationId);
  add("conversation", delivery.chatId);
  add("thread", delivery.threadId);
  add("target", delivery.to);
  if (senderId) aliases.push(`sender:${channel}:${senderId}`);
  return [...new Set(aliases)];
}

export function preferredPolicyKey(aliases: string[]): string {
  return (
    aliases.find((alias) => alias.startsWith("session:")) ||
    aliases.find((alias) => alias.startsWith("sender:")) ||
    aliases.find((alias) => alias.startsWith("conversation:")) ||
    aliases[0] ||
    "unknown"
  );
}

export function resolveUserId(
  config: PluginConfig,
  senderId: string | undefined,
  channel: string,
): string | undefined {
  if (senderId && config.senderMap[senderId]) return config.senderMap[senderId];
  if (senderId && config.ownerSenderIds.includes(senderId)) return config.defaultUserId;
  if (!senderId) {
    if (config.onlyUserTurns && channel === "unknown") return undefined;
    return config.unknownSenderPolicy === "skip" ? undefined : config.defaultUserId;
  }
  if (config.unknownSenderPolicy === "skip") return undefined;
  if (config.unknownSenderPolicy === "pseudonymous") {
    return `wechat-${createHash("sha256")
      .update(`${channel}\u0000${senderId}`)
      .digest("hex")
      .slice(0, 20)}`;
  }
  return config.defaultUserId;
}

export function channelIsAllowed(config: PluginConfig, channel: string): boolean {
  return config.allowedChannels.length === 0 || config.allowedChannels.includes(channel);
}

export function parseStickerCandidates(value: unknown): StickerCandidate[] {
  if (!Array.isArray(value)) return [];
  const candidates: StickerCandidate[] = [];
  const seen = new Set<string>();
  for (const valueItem of value) {
    const item = asRecord(valueItem);
    const id = optionalString(item.id);
    const file = optionalString(item.file);
    const description = optionalString(item.description);
    if (
      !id ||
      !file ||
      !description ||
      !STICKER_ID_PATTERN.test(id) ||
      !STICKER_FILE_PATTERN.test(file) ||
      file.split("/").includes("..") ||
      seen.has(id)
    ) {
      continue;
    }
    seen.add(id);
    candidates.push({
      id,
      file,
      description: description.slice(0, 1000),
      score: numberValue(item.score, 0, 0, 100000),
      scenes: stringArray(item.scenes).slice(0, 10),
    });
  }
  return candidates.slice(0, 20);
}

export function runBridge(
  config: PluginConfig,
  request: { user_id: string; message: string },
): Promise<BridgeResult> {
  return new Promise((resolvePromise, rejectPromise) => {
    const pythonSource = join(config.repoPath, "social_layer", "src");
    if (!existsSync(pythonSource)) return rejectPromise(new Error("python_source_missing"));
    const args = [
      "-m",
      "yogurt_social.openclaw_adapter",
      "--db",
      config.dbPath,
      "--max-memories",
      String(config.maxMemories),
      "--project-root",
      config.repoPath,
    ];
    if (config.stickerEnabled) args.push("--sticker-config", config.stickerConfigPath);
    const child = spawn(config.pythonPath, args, {
      cwd: config.repoPath,
      env: {
        ...process.env,
        PYTHONPATH: process.env.PYTHONPATH
          ? `${pythonSource}${delimiter}${process.env.PYTHONPATH}`
          : pythonSource,
        YOGURT_SOCIAL_DB: config.dbPath,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let settled = false;
    const rejectOnce = (reason: string) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      rejectPromise(new Error(reason));
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      rejectOnce("bridge_timeout");
    }, config.timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
      if (Buffer.byteLength(stdout, "utf8") > MAX_BRIDGE_OUTPUT_BYTES) {
        child.kill("SIGKILL");
        rejectOnce("bridge_output_too_large");
      }
    });
    child.stderr.on("data", () => {});
    child.on("error", () => rejectOnce("bridge_spawn_failed"));
    child.on("close", (code) => {
      clearTimeout(timer);
      if (settled) return;
      if (code !== 0) return rejectOnce(`bridge_exit_${String(code)}`);
      try {
        settled = true;
        resolvePromise(JSON.parse(stdout) as BridgeResult);
      } catch {
        rejectOnce("bridge_invalid_json");
      }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

export function messageForbidsSticker(message: string): boolean {
  return /(不要|别|不用|禁止|不需要).{0,8}(发|用|来|给).{0,8}(表情包|表情|贴图|动图)/.test(
    message.replace(/\s+/g, ""),
  );
}

export function messageExplicitlyRequestsSticker(message: string): boolean {
  const normalized = message.replace(/\s+/g, "");
  return (
    /(表情包|表情|贴图|动图)/.test(normalized) &&
    /(发|来|给我|看看|看下|用|整|丢|甩|送)/.test(normalized) &&
    !messageForbidsSticker(message)
  );
}

export function stripStickerCandidateContext(value: string): string {
  const markerIndex = value.indexOf(STICKER_CONTEXT_MARKER);
  return markerIndex >= 0 ? value.slice(0, markerIndex).trimEnd() : value.trim();
}

export function buildAutoStickerContext(candidate: StickerCandidate): string {
  return [
    "[Yogurt Sticker Plan]",
    "系统将在本轮文字回复发送完成后自动发送一张表情包。",
    `表情描述：${candidate.description}`,
    "让正文自然配合这个表情，但不要提及表情包 ID、文件名、本地路径或系统选择过程。",
    "不要调用 choose_sticker。",
  ].join("\n");
}

export function resolveStickerFile(config: PluginConfig, candidate: StickerCandidate): string {
  const root = realpathSync(join(config.repoPath, "assets", "stickers"));
  const candidatePath = realpathSync(resolve(root, candidate.file));
  if (candidatePath !== root && !candidatePath.startsWith(`${root}${sep}`)) {
    throw new Error("sticker_path_outside_root");
  }
  if (!statSync(candidatePath).isFile()) throw new Error("sticker_file_not_regular");
  return candidatePath;
}
