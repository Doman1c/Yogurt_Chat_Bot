import { spawn } from "node:child_process";
import { delimiter, join } from "node:path";

import { assistantTextFromEvent } from "./sticker-v21-utils.ts";
import {
  asRecord,
  channelFromContext,
  channelIsAllowed,
  contextAliases,
  optionalString,
  readConfig,
  resolveUserId,
  senderIdFromContext,
} from "./sticker-plugin-utils.ts";
import {
  addTurnCorrelationAliases,
  CorrelationStore,
} from "./turn-correlation.ts";

const PLUGIN_ID = "yogurt-social-injector";
const MAX_PENDING_TURNS = 200;
const TURN_TTL_MS = 15 * 60 * 1000;
const MAX_OUTPUT_BYTES = 256 * 1024;

type PendingTurn = {
  userId: string;
  message: string;
};

type ExtensionConfig = {
  enabled: boolean;
  promotionThreshold: number;
  timeoutMs: number;
};

type ExtensionResult = {
  ok?: boolean;
  candidate_count?: number;
  promoted_count?: number;
  pending_count?: number;
  conflict_count?: number;
};

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
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

function readExtensionConfig(api: any, event?: any): ExtensionConfig {
  const raw = {
    ...asRecord(api.pluginConfig),
    ...asRecord(asRecord(event?.context).pluginConfig),
  };
  return {
    enabled: booleanValue(raw.autoMemoryEnabled, false),
    promotionThreshold: numberValue(
      raw.autoMemoryPromotionThreshold,
      0.78,
      0.5,
      1,
    ),
    timeoutMs: integerValue(raw.autoMemoryTimeoutMs, 2500, 500, 10000),
  };
}

function eventMetadata(event?: any): Record<string, unknown> {
  return asRecord(event?.metadata);
}

function eventChannel(event: any, ctx: any): string {
  return (
    optionalString(event?.channel) ||
    optionalString(eventMetadata(event).channel) ||
    channelFromContext(ctx)
  );
}

function targetFromContext(ctx: any, event?: any): string | undefined {
  return (
    optionalString(event?.to) ||
    optionalString(ctx?.to) ||
    optionalString(ctx?.chatId) ||
    optionalString(ctx?.conversationId) ||
    optionalString(ctx?.channelContext?.chat?.id) ||
    senderIdFromContext(ctx)
  );
}

function aliasesFor(ctx: any, event?: any): string[] {
  const channel = eventChannel(event, ctx);
  const senderId = senderIdFromContext(ctx);
  const target = targetFromContext(ctx, event);
  const aliases = contextAliases(ctx, channel, senderId, event);
  if (target) aliases.push(`peer:${channel}:${target}`);
  return addTurnCorrelationAliases(aliases, event, ctx);
}

const turns = new CorrelationStore<PendingTurn>(TURN_TTL_MS, MAX_PENDING_TURNS);

function runExtension(
  api: any,
  turn: PendingTurn,
  assistantReply: string,
  config: ExtensionConfig,
): Promise<ExtensionResult> {
  const baseConfig = readConfig(api);
  return new Promise((resolvePromise, rejectPromise) => {
    const pythonSource = join(baseConfig.repoPath, "social_layer", "src");
    const child = spawn(
      baseConfig.pythonPath,
      [
        "-m",
        "yogurt_social.auto_memory_extended",
        "--db",
        baseConfig.dbPath,
        "--promotion-threshold",
        String(config.promotionThreshold),
      ],
      {
        cwd: baseConfig.repoPath,
        env: {
          ...process.env,
          PYTHONPATH: process.env.PYTHONPATH
            ? `${pythonSource}${delimiter}${process.env.PYTHONPATH}`
            : pythonSource,
          YOGURT_SOCIAL_DB: baseConfig.dbPath,
        },
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

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
      rejectOnce("auto_memory_extension_timeout");
    }, config.timeoutMs);

    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
      if (Buffer.byteLength(stdout, "utf8") > MAX_OUTPUT_BYTES) {
        child.kill("SIGKILL");
        rejectOnce("auto_memory_extension_output_too_large");
      }
    });
    child.stderr.on("data", () => {});
    child.on("error", () => rejectOnce("auto_memory_extension_spawn_failed"));
    child.on("close", (code) => {
      clearTimeout(timer);
      if (settled) return;
      if (code !== 0) return rejectOnce(`auto_memory_extension_exit_${String(code)}`);
      try {
        settled = true;
        resolvePromise(JSON.parse(stdout) as ExtensionResult);
      } catch {
        rejectOnce("auto_memory_extension_invalid_json");
      }
    });
    child.stdin.end(
      JSON.stringify({
        user_id: turn.userId,
        user_message: turn.message,
        assistant_reply: assistantReply,
      }),
    );
  });
}

export function registerExtendedMemoryRuntime(api: any): void {
  const commit = async (
    trigger: "agent_end" | "message_sent" | "message_sending",
    aliases: string[],
    assistantReply: string | undefined,
    event?: any,
  ): Promise<void> => {
    const config = readExtensionConfig(api, event);
    if (!config.enabled || !assistantReply) return;
    const turn = turns.consume(aliases);
    if (!turn) return;
    try {
      const result = await runExtension(api, turn, assistantReply, config);
      if ((result.candidate_count || 0) === 0) return;
      api.logger.info(
        `${PLUGIN_ID}: auto memory extension trigger=${trigger} candidates=${result.candidate_count || 0} promoted=${result.promoted_count || 0} pending=${result.pending_count || 0} conflicts=${result.conflict_count || 0}`,
      );
    } catch (error) {
      const reason = error instanceof Error ? error.message : "unknown_error";
      api.logger.warn(
        `${PLUGIN_ID}: auto memory extension unavailable (${reason}); chat preserved`,
      );
    }
  };

  api.on(
    "before_prompt_build",
    async (event: any, ctx: any) => {
      const baseConfig = readConfig(api, event);
      const config = readExtensionConfig(api, event);
      if (!baseConfig.enabled || !config.enabled) return;
      const prompt = optionalString(event?.prompt);
      if (!prompt || ctx?.jobId) return;
      const channel = channelFromContext(ctx);
      if (!channelIsAllowed(baseConfig, channel)) return;
      const userId = resolveUserId(baseConfig, senderIdFromContext(ctx), channel);
      if (!userId) return;
      turns.register(aliasesFor(ctx, event), { userId, message: prompt });
    },
    { priority: 54, timeoutMs: 2000 },
  );

  api.on("agent_end", async (event: any, ctx: any) => {
    if (event?.success !== true) return;
    await commit(
      "agent_end",
      aliasesFor(ctx, event),
      assistantTextFromEvent(event, ctx),
      event,
    );
  });

  api.on("message_sent", async (event: any, ctx: any) => {
    if (event?.success !== true) return;
    await commit(
      "message_sent",
      aliasesFor(ctx, event),
      optionalString(event?.content),
      event,
    );
  });

  api.on("message_sending", async (event: any, ctx: any) => {
    const text = optionalString(event?.content) || optionalString(event?.text);
    if (!text) return;
    await commit("message_sending", aliasesFor(ctx, event), text, event);
  });

  api.logger.info(
    `${PLUGIN_ID}: V3 incremental memory runtime registered device-feedback patterns`,
  );
}
