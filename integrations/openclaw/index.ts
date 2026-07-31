import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, delimiter, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { StickerSelectionStore } from "./sticker-selection.ts";

const PLUGIN_ID = "yogurt-social-injector";
const DEFAULT_TIMEOUT_MS = 2500;
const MAX_BRIDGE_OUTPUT_BYTES = 1024 * 1024;
const STICKER_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$/;
const STICKER_FILE_PATTERN = /^images\/[A-Za-z0-9][A-Za-z0-9._/-]*\.(png|jpg|jpeg|webp|gif)$/i;

type UnknownSenderPolicy = "default" | "pseudonymous" | "skip";

type PluginConfig = {
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
};

type BridgeStickerCandidate = {
  id: string;
  file: string;
  description: string;
};

type BridgeResult = {
  original_message?: unknown;
  social_context?: unknown;
  sticker_candidates?: unknown;
  final_model_input?: unknown;
  metadata?: Record<string, unknown>;
  error?: unknown;
};

const selectionStore = new StickerSelectionStore();

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
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

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

function stringMap(value: unknown): Record<string, string> {
  const record = asRecord(value);
  return Object.fromEntries(
    Object.entries(record).filter(
      (entry): entry is [string, string] =>
        typeof entry[1] === "string" && entry[1].trim().length > 0,
    ),
  );
}

function expandHome(value: string): string {
  if (value === "~") return homedir();
  if (value.startsWith("~/")) return join(homedir(), value.slice(2));
  return value;
}

function expandPath(api: any, value: string): string {
  const fallback = expandHome(value);
  if (typeof api.resolvePath !== "function") {
    return fallback;
  }

  try {
    const resolved = api.resolvePath(value);
    return typeof resolved === "string" && resolved.length > 0
      ? resolved
      : fallback;
  } catch {
    return fallback;
  }
}

function readConfig(api: any, event?: any): PluginConfig {
  const registrationConfig = asRecord(api.pluginConfig);
  const eventContext = asRecord(event?.context);
  const eventConfig = asRecord(eventContext.pluginConfig);
  const raw = { ...registrationConfig, ...eventConfig };
  const pluginDir = dirname(fileURLToPath(import.meta.url));
  const inferredRepoPath = resolve(pluginDir, "../..");
  const defaultDbPath =
    process.env.YOGURT_SOCIAL_DB || "~/.openclaw/yogurt-social/memory.sqlite3";
  const unknownSenderPolicy =
    raw.unknownSenderPolicy === "pseudonymous" || raw.unknownSenderPolicy === "skip"
      ? raw.unknownSenderPolicy
      : "default";

  return {
    enabled: booleanValue(raw.enabled, true),
    pythonPath: stringValue(raw.pythonPath, "python3"),
    repoPath: expandPath(api, stringValue(raw.repoPath, inferredRepoPath)),
    dbPath: expandPath(api, stringValue(raw.dbPath, defaultDbPath)),
    defaultUserId: stringValue(raw.defaultUserId, "owner"),
    senderMap: stringMap(raw.senderMap),
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
  };
}

function senderIdFromContext(ctx: any): string | undefined {
  const direct = optionalString(ctx?.senderId);
  const requesterDirect = optionalString(ctx?.requesterSenderId);
  const nested = optionalString(ctx?.channelContext?.sender?.id);
  const requester = optionalString(ctx?.requester?.senderId);
  const delivery = asRecord(ctx?.deliveryContext);
  const deliverySender = optionalString(delivery.requesterSenderId || delivery.senderId);
  return direct || requesterDirect || nested || requester || deliverySender;
}

function channelFromContext(ctx: any): string {
  if (typeof ctx?.channel === "string") return ctx.channel;
  if (typeof ctx?.messageProvider === "string") return ctx.messageProvider;
  if (typeof ctx?.requester?.channel === "string") return ctx.requester.channel;
  const delivery = asRecord(ctx?.deliveryContext);
  if (typeof delivery.channel === "string") return delivery.channel;
  if (typeof delivery.provider === "string") return delivery.provider;
  return "unknown";
}

function contextAliases(
  ctx: any,
  channel: string,
  senderId?: string,
): string[] {
  const aliases: string[] = [];
  const add = (prefix: string, value: unknown) => {
    const resolved = optionalString(value);
    if (resolved) aliases.push(`${prefix}:${resolved}`);
  };

  add("run", ctx?.runId);
  add("session", ctx?.sessionKey);
  add("session-id", ctx?.sessionId);
  add("native", ctx?.nativeChannelId);

  const delivery = asRecord(ctx?.deliveryContext);
  add("native", delivery.nativeChannelId);
  add("conversation", delivery.conversationId);
  add("conversation", delivery.chatId);
  add("thread", delivery.threadId);
  add("target", delivery.to);

  if (senderId) aliases.push(`sender:${channel}:${senderId}`);
  return [...new Set(aliases)];
}

function resolveUserId(
  config: PluginConfig,
  senderId: string | undefined,
  channel: string,
): string | undefined {
  if (senderId && config.senderMap[senderId]) {
    return config.senderMap[senderId];
  }
  if (senderId && config.ownerSenderIds.includes(senderId)) {
    return config.defaultUserId;
  }
  if (!senderId) {
    if (config.onlyUserTurns && channel === "unknown") return undefined;
    return config.unknownSenderPolicy === "skip" ? undefined : config.defaultUserId;
  }
  if (config.unknownSenderPolicy === "skip") {
    return undefined;
  }
  if (config.unknownSenderPolicy === "pseudonymous") {
    const digest = createHash("sha256")
      .update(`${channel}\u0000${senderId}`)
      .digest("hex")
      .slice(0, 20);
    return `wechat-${digest}`;
  }
  return config.defaultUserId;
}

function channelIsAllowed(config: PluginConfig, channel: string): boolean {
  return config.allowedChannels.length === 0 || config.allowedChannels.includes(channel);
}

function parseStickerCandidates(value: unknown): BridgeStickerCandidate[] {
  if (!Array.isArray(value)) return [];
  const candidates: BridgeStickerCandidate[] = [];
  const seen = new Set<string>();
  for (const rawItem of value) {
    const item = asRecord(rawItem);
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
    candidates.push({ id, file, description: description.slice(0, 1000) });
  }
  return candidates.slice(0, 20);
}

function runBridge(
  config: PluginConfig,
  request: { user_id: string; message: string },
): Promise<BridgeResult> {
  return new Promise((resolvePromise, rejectPromise) => {
    const pythonSource = join(config.repoPath, "social_layer", "src");
    if (!existsSync(pythonSource)) {
      rejectPromise(new Error("python_source_missing"));
      return;
    }

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
    if (config.stickerEnabled) {
      args.push("--sticker-config", config.stickerConfigPath);
    }

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
    let timer: NodeJS.Timeout | undefined;

    const finishReject = (reason: string) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      rejectPromise(new Error(reason));
    };

    timer = setTimeout(() => {
      child.kill("SIGKILL");
      finishReject("bridge_timeout");
    }, config.timeoutMs);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
      if (Buffer.byteLength(stdout, "utf8") > MAX_BRIDGE_OUTPUT_BYTES) {
        child.kill("SIGKILL");
        finishReject("bridge_output_too_large");
      }
    });
    child.stderr.on("data", () => {});
    child.on("error", () => finishReject("bridge_spawn_failed"));
    child.on("close", (code) => {
      if (timer) clearTimeout(timer);
      if (settled) return;
      if (code !== 0) {
        finishReject(`bridge_exit_${String(code)}`);
        return;
      }
      try {
        const parsed = JSON.parse(stdout) as BridgeResult;
        settled = true;
        resolvePromise(parsed);
      } catch {
        finishReject("bridge_invalid_json");
      }
    });

    child.stdin.end(JSON.stringify(request));
  });
}

const chooseStickerParameters = {
  type: "object",
  additionalProperties: false,
  required: ["sticker_id"],
  properties: {
    sticker_id: {
      type: "string",
      minLength: 3,
      maxLength: 80,
      description: "本轮 [Yogurt Sticker Candidates] 中列出的 sticker_id",
    },
  },
};

const chooseStickerOutput = {
  type: "object",
  additionalProperties: false,
  required: ["status"],
  properties: {
    status: { type: "string", enum: ["selected", "rejected"] },
    sticker_id: { type: "string" },
    reason: { type: "string" },
    idempotent: { type: "boolean" },
  },
};

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Yogurt Social Injector",
  description: "Injects Yogurt social context and bounded V2 sticker candidates.",
  register(api) {
    if (typeof api.registerTool === "function") {
      api.registerTool((toolContext: any) => ({
        name: "choose_sticker",
        label: "Choose Yogurt Sticker",
        description:
          "选择本轮候选列表中的一张表情包。可以不调用；每轮最多选择一张。该工具只记录选择，不立即发送图片。",
        parameters: chooseStickerParameters as any,
        outputSchema: chooseStickerOutput as any,
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          const channel = channelFromContext(toolContext);
          const senderId = senderIdFromContext(toolContext);
          const aliases = contextAliases(toolContext, channel, senderId);
          const stickerId = optionalString(params.sticker_id);
          if (!stickerId || !STICKER_ID_PATTERN.test(stickerId)) {
            return {
              content: [
                {
                  type: "text",
                  text: "表情包选择被拒绝：sticker_id 无效。请继续完成纯文本回复。",
                },
              ],
              details: { status: "rejected", reason: "invalid_sticker_id" },
            };
          }

          const result = selectionStore.select(aliases, stickerId);
          if (result.status === "rejected") {
            return {
              content: [
                {
                  type: "text",
                  text: "表情包选择未生效：该 ID 不属于当前有效候选，或候选已过期。请继续完成纯文本回复。",
                },
              ],
              details: { status: "rejected", reason: result.reason },
            };
          }

          api.logger.info(`${PLUGIN_ID}: sticker selection pending`);
          return {
            content: [
              {
                type: "text",
                text: "已记录本轮表情包选择。请继续完成文本回复，不要在回复中输出表情包 ID。",
              },
            ],
            details: {
              status: "selected",
              sticker_id: result.sticker.id,
              idempotent: result.idempotent,
            },
          };
        },
      }));
    } else {
      api.logger.warn(`${PLUGIN_ID}: registerTool unavailable; sticker selection disabled`);
    }

    api.on(
      "before_prompt_build",
      async (event, ctx) => {
        const config = readConfig(api, event);
        if (!config.enabled) return;

        const prompt = typeof event.prompt === "string" ? event.prompt : "";
        if (!prompt) return;

        const channel = channelFromContext(ctx);
        if (!channelIsAllowed(config, channel)) return;
        if (ctx?.jobId) return;

        const senderId = senderIdFromContext(ctx);
        const userId = resolveUserId(config, senderId, channel);
        if (!userId) return;
        const aliases = contextAliases(ctx, channel, senderId);

        const startedAt = Date.now();
        api.logger.info(
          `${PLUGIN_ID}: injection start channel=${channel} promptChars=${prompt.length}`,
        );

        try {
          const bridgeResult = await runBridge(config, {
            user_id: userId,
            message: prompt,
          });
          const socialContext =
            typeof bridgeResult.social_context === "string"
              ? bridgeResult.social_context.trim()
              : "";
          const candidates = config.stickerEnabled
            ? parseStickerCandidates(bridgeResult.sticker_candidates)
            : [];

          if (candidates.length > 0) {
            selectionStore.register(aliases, candidates);
          } else {
            selectionStore.clear(aliases);
          }

          if (!socialContext) {
            api.logger.info(
              `${PLUGIN_ID}: bridge returned no context in ${Date.now() - startedAt}ms`,
            );
            return;
          }

          api.logger.info(
            `${PLUGIN_ID}: injection ready bridgeMs=${Date.now() - startedAt} contextChars=${socialContext.length} stickerCandidates=${candidates.length}`,
          );
          return { prependContext: socialContext };
        } catch (error) {
          selectionStore.clear(aliases);
          const reason = error instanceof Error ? error.message : "unknown_error";
          api.logger.warn(`${PLUGIN_ID}: social layer unavailable (${reason}); fail-open`);
          return;
        }
      },
      { priority: 60, timeoutMs: 12000 },
    );

    api.logger.info(`${PLUGIN_ID}: registered before_prompt_build and choose_sticker`);
  },
});
