import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { dirname, delimiter, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const PLUGIN_ID = "yogurt-social-injector";
const DEFAULT_TIMEOUT_MS = 2500;
const MAX_BRIDGE_OUTPUT_BYTES = 1024 * 1024;

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
};

type BridgeResult = {
  original_message?: unknown;
  social_context?: unknown;
  final_model_input?: unknown;
  metadata?: Record<string, unknown>;
  error?: unknown;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
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

function expandPath(api: any, value: string): string {
  return typeof api.resolvePath === "function" ? api.resolvePath(value) : value;
}

function readConfig(api: any): PluginConfig {
  const raw = asRecord(api.pluginConfig);
  const pluginDir = dirname(fileURLToPath(import.meta.url));
  const inferredRepoPath = resolve(pluginDir, "../..");
  const defaultDbPath = process.env.YOGURT_SOCIAL_DB || "~/.openclaw/yogurt-social/memory.sqlite3";
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
  };
}

function senderIdFromContext(ctx: any): string | undefined {
  const direct = typeof ctx?.senderId === "string" ? ctx.senderId : undefined;
  const nested =
    typeof ctx?.channelContext?.sender?.id === "string"
      ? ctx.channelContext.sender.id
      : undefined;
  const requester =
    typeof ctx?.requester?.senderId === "string"
      ? ctx.requester.senderId
      : undefined;
  return direct || nested || requester;
}

function channelFromContext(ctx: any): string {
  if (typeof ctx?.channel === "string") return ctx.channel;
  if (typeof ctx?.messageProvider === "string") return ctx.messageProvider;
  return "unknown";
}

function resolveUserId(config: PluginConfig, senderId: string | undefined, channel: string): string | undefined {
  if (senderId && config.senderMap[senderId]) {
    return config.senderMap[senderId];
  }
  if (senderId && config.ownerSenderIds.includes(senderId)) {
    return config.defaultUserId;
  }
  if (!senderId) {
    // Background/system turns normally have no channel identity at all. Some
    // channel adapters can identify the channel before they expose senderId;
    // keep the single-owner V1 fallback working in that case.
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

    const child = spawn(
      config.pythonPath,
      [
        "-m",
        "yogurt_social.openclaw_adapter",
        "--db",
        config.dbPath,
        "--max-memories",
        String(config.maxMemories),
      ],
      {
        cwd: config.repoPath,
        env: {
          ...process.env,
          PYTHONPATH: process.env.PYTHONPATH
            ? `${pythonSource}${delimiter}${process.env.PYTHONPATH}`
            : pythonSource,
          YOGURT_SOCIAL_DB: config.dbPath,
        },
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

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
    // Drain stderr so a noisy Python failure cannot block on a full pipe.
    // The content is intentionally not forwarded to the model or plugin logs.
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

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Yogurt Social Injector",
  description: "Injects Yogurt Social Layer V1 context before each eligible model turn.",
  register(api) {
    const config = readConfig(api);

    api.on(
      "before_prompt_build",
      async (event, ctx) => {
        if (!config.enabled) return;
        const prompt = typeof event.prompt === "string" ? event.prompt : "";
        if (!prompt) return;

        const channel = channelFromContext(ctx);
        if (!channelIsAllowed(config, channel)) return;
        if (ctx?.jobId) return;

        const senderId = senderIdFromContext(ctx);
        const userId = resolveUserId(config, senderId, channel);
        if (!userId) return;

        try {
          const bridgeResult = await runBridge(config, {
            user_id: userId,
            message: prompt,
          });
          const socialContext =
            typeof bridgeResult.social_context === "string"
              ? bridgeResult.social_context.trim()
              : "";
          if (!socialContext) return;

          // Add context separately. Never replace or concatenate event.prompt.
          return { prependSystemContext: socialContext };
        } catch (error) {
          const reason = error instanceof Error ? error.message : "unknown_error";
          api.logger.warn(`${PLUGIN_ID}: social layer unavailable (${reason}); fail-open`);
          return;
        }
      },
      { priority: 60, timeoutMs: 12000 },
    );

    api.logger.info(`${PLUGIN_ID}: registered before_prompt_build hook`);
  },
});
