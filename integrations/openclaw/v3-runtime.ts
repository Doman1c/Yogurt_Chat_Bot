import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { delimiter, join } from "node:path";

import { splitNaturalBubbles } from "./multi-bubble.ts";
import { assistantTextFromEvent } from "./sticker-v21-utils.ts";
import {
  asRecord,
  channelFromContext,
  channelIsAllowed,
  contextAliases,
  optionalString,
  preferredPolicyKey,
  readConfig,
  resolveUserId,
  senderIdFromContext,
} from "./sticker-plugin-utils.ts";

const PLUGIN_ID = "yogurt-social-injector";
const WEIXIN_CHANNEL = "openclaw-weixin";
const MAX_PENDING_TURNS = 200;
const TURN_TTL_MS = 15 * 60 * 1000;
const MAX_AUTO_MEMORY_OUTPUT_BYTES = 256 * 1024;

type V3Config = {
  autoMemoryEnabled: boolean;
  autoMemoryPromotionThreshold: number;
  autoMemoryTimeoutMs: number;
  multiBubbleEnabled: boolean;
  multiBubbleMinChars: number;
  multiBubbleMaxChars: number;
  multiBubbleMaxBubbles: number;
  multiBubbleDelayMs: number;
  memoryServiceEnabled: boolean;
  memoryServiceUrl: string;
  memoryServiceTimeoutMs: number;
  memoryServicePersonaId: string;
};

type PendingTurn = {
  userId: string;
  message: string;
  aliases: string[];
  conversationKey: string;
  surface: string;
  turnId: string;
  createdAt: number;
};

type AutoMemoryResult = {
  ok?: boolean;
  candidate_count?: number;
  promoted_count?: number;
  pending_count?: number;
  conflict_count?: number;
};

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

function readV3Config(api: any, event?: any): V3Config {
  const raw = {
    ...asRecord(api.pluginConfig),
    ...asRecord(asRecord(event?.context).pluginConfig),
  };
  return {
    autoMemoryEnabled: booleanValue(raw.autoMemoryEnabled, false),
    autoMemoryPromotionThreshold: numberValue(
      raw.autoMemoryPromotionThreshold,
      0.78,
      0.5,
      1,
    ),
    autoMemoryTimeoutMs: integerValue(raw.autoMemoryTimeoutMs, 2500, 500, 10000),
    multiBubbleEnabled: booleanValue(raw.multiBubbleEnabled, false),
    multiBubbleMinChars: integerValue(raw.multiBubbleMinChars, 24, 8, 200),
    multiBubbleMaxChars: integerValue(raw.multiBubbleMaxChars, 360, 80, 2000),
    multiBubbleMaxBubbles: integerValue(raw.multiBubbleMaxBubbles, 3, 2, 4),
    multiBubbleDelayMs: integerValue(raw.multiBubbleDelayMs, 420, 0, 3000),
    memoryServiceEnabled: booleanValue(raw.memoryServiceEnabled, false),
    memoryServiceUrl: typeof raw.memoryServiceUrl === "string" && /^http:\/\/127\.0\.0\.1(?::\d+)?$/i.test(raw.memoryServiceUrl.trim())
      ? raw.memoryServiceUrl.trim()
      : "http://127.0.0.1:17621",
    memoryServiceTimeoutMs: integerValue(raw.memoryServiceTimeoutMs, 450, 100, 5000),
    memoryServicePersonaId: typeof raw.memoryServicePersonaId === "string" && raw.memoryServicePersonaId.trim()
      ? raw.memoryServicePersonaId.trim().slice(0, 180)
      : "yogurt",
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

function accountIdFromContext(ctx: any, event?: any): string | undefined {
  return (
    optionalString(event?.accountId) ||
    optionalString(eventMetadata(event).accountId) ||
    optionalString(ctx?.accountId) ||
    optionalString(ctx?.channelContext?.accountId) ||
    optionalString(ctx?.deliveryContext?.accountId)
  );
}

function eventHasMedia(event: any): boolean {
  const metadata = eventMetadata(event);
  return Boolean(
    optionalString(event?.mediaUrl) ||
      (Array.isArray(event?.mediaUrls) && event.mediaUrls.length > 0) ||
      optionalString(metadata.mediaUrl) ||
      (Array.isArray(metadata.mediaUrls) && metadata.mediaUrls.length > 0),
  );
}

function aliasesFor(ctx: any, event?: any): string[] {
  const channel = eventChannel(event, ctx);
  const senderId = senderIdFromContext(ctx);
  const target = targetFromContext(ctx, event);
  const aliases = contextAliases(ctx, channel, senderId, event);
  if (target) aliases.push(`peer:${channel}:${target}`);
  return [...new Set(aliases)];
}

class PendingTurnStore {
  private readonly byAlias = new Map<string, PendingTurn>();

  register(aliases: string[], turn: Omit<PendingTurn, "aliases" | "createdAt">): void {
    this.prune();
    const resolvedAliases = [...new Set(aliases)].filter(Boolean);
    if (resolvedAliases.length === 0) return;
    const entry: PendingTurn = {
      ...turn,
      aliases: resolvedAliases,
      createdAt: Date.now(),
    };
    for (const alias of resolvedAliases) this.byAlias.set(alias, entry);
    if (this.byAlias.size > MAX_PENDING_TURNS) this.prune(true);
  }

  get(aliases: string[]): PendingTurn | undefined {
    this.prune();
    for (const alias of aliases) {
      const turn = this.byAlias.get(alias);
      if (turn) return turn;
    }
    return undefined;
  }

  consume(aliases: string[]): PendingTurn | undefined {
    const turn = this.get(aliases);
    if (!turn) return undefined;
    for (const alias of turn.aliases) {
      if (this.byAlias.get(alias) === turn) this.byAlias.delete(alias);
    }
    return turn;
  }

  private prune(force = false): void {
    const cutoff = Date.now() - TURN_TTL_MS;
    const seen = new Set<PendingTurn>(this.byAlias.values());
    const ordered = [...seen].sort((left, right) => left.createdAt - right.createdAt);
    for (const turn of ordered) {
      if (!force && turn.createdAt >= cutoff) continue;
      for (const alias of turn.aliases) {
        if (this.byAlias.get(alias) === turn) this.byAlias.delete(alias);
      }
      if (force && this.byAlias.size <= MAX_PENDING_TURNS / 2) break;
    }
  }
}

const memoryTurns = new PendingTurnStore();
const bubbleTurns = new PendingTurnStore();
const bubblePassThrough = new Map<string, Set<symbol>>();

function bubbleFingerprint(to: string, accountId: string | undefined, text: string): string {
  return `${accountId || "default"}\0${to}\0${text}`;
}

function addBubblePassThrough(fingerprint: string): symbol {
  const token = Symbol(fingerprint);
  const tokens = bubblePassThrough.get(fingerprint) || new Set<symbol>();
  tokens.add(token);
  bubblePassThrough.set(fingerprint, tokens);
  return token;
}

function consumeBubblePassThrough(fingerprint: string): boolean {
  const tokens = bubblePassThrough.get(fingerprint);
  const token = tokens?.values().next().value as symbol | undefined;
  if (!tokens || !token) return false;
  tokens.delete(token);
  if (tokens.size === 0) bubblePassThrough.delete(fingerprint);
  return true;
}

function releaseBubblePassThrough(fingerprint: string, token: symbol): void {
  const tokens = bubblePassThrough.get(fingerprint);
  if (!tokens) return;
  tokens.delete(token);
  if (tokens.size === 0) bubblePassThrough.delete(fingerprint);
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function runAutoMemory(
  api: any,
  turn: PendingTurn,
  assistantReply: string,
  config: V3Config,
): Promise<AutoMemoryResult> {
  const baseConfig = readConfig(api);
  return new Promise((resolvePromise, rejectPromise) => {
    const pythonSource = join(baseConfig.repoPath, "social_layer", "src");
    const child = spawn(
      baseConfig.pythonPath,
      [
        "-m",
        "yogurt_social.auto_memory",
        "--db",
        baseConfig.dbPath,
        "--promotion-threshold",
        String(config.autoMemoryPromotionThreshold),
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
      rejectOnce("auto_memory_timeout");
    }, config.autoMemoryTimeoutMs);

    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
      if (Buffer.byteLength(stdout, "utf8") > MAX_AUTO_MEMORY_OUTPUT_BYTES) {
        child.kill("SIGKILL");
        rejectOnce("auto_memory_output_too_large");
      }
    });
    child.stderr.on("data", () => {});
    child.on("error", () => rejectOnce("auto_memory_spawn_failed"));
    child.on("close", (code) => {
      clearTimeout(timer);
      if (settled) return;
      if (code !== 0) return rejectOnce(`auto_memory_exit_${String(code)}`);
      try {
        settled = true;
        resolvePromise(JSON.parse(stdout) as AutoMemoryResult);
      } catch {
        rejectOnce("auto_memory_invalid_json");
      }
    });
    child.stdin.end(
      JSON.stringify({
        user_id: turn.userId,
        user_message: turn.message,
        assistant_reply: assistantReply,
        idempotency_key: turn.turnId,
      }),
    );
  });
}

async function runMemoryServiceObserve(
  config: V3Config,
  turn: PendingTurn,
  assistantReply: string,
): Promise<AutoMemoryResult | undefined> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.memoryServiceTimeoutMs);
  try {
    const response = await fetch(`${config.memoryServiceUrl}/v1/memory/observe`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        persona_id: config.memoryServicePersonaId,
        user_id: turn.userId,
        surface: turn.surface,
        conversation_key: turn.conversationKey,
        phase: "assistant",
        user_message: turn.message,
        assistant_reply: assistantReply,
        idempotency_key: turn.turnId,
      }),
      signal: controller.signal,
    });
    if (!response.ok) return undefined;
    const value: unknown = await response.json();
    if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
    const item = value as Record<string, unknown>;
    return {
      ok: item.ok === true,
      candidate_count: typeof item.candidate_count === "number" ? item.candidate_count : 0,
      promoted_count: typeof item.promoted_count === "number" ? item.promoted_count : 0,
      conflict_count: typeof item.conflict_count === "number" ? item.conflict_count : 0,
    };
  } catch {
    return undefined;
  } finally {
    clearTimeout(timer);
  }
}

export function registerV3Runtime(api: any): void {
  const commitMemory = async (
    trigger: "agent_end" | "message_sent" | "message_sending",
    aliases: string[],
    assistantReply: string | undefined,
    event?: any,
  ): Promise<boolean> => {
    const config = readV3Config(api, event);
    if (!config.autoMemoryEnabled || !assistantReply) return false;
    const turn = memoryTurns.consume(aliases);
    if (!turn) return false;
    try {
      const serviceResult = config.memoryServiceEnabled
        ? await runMemoryServiceObserve(config, turn, assistantReply)
        : undefined;
      // A timeout is ambiguous: the service may have committed canonical V3
      // state after the client stopped waiting. Do not run a second writer and
      // inflate occurrences/revisions. Chat itself always remains fail-open.
      if (config.memoryServiceEnabled && !serviceResult) {
        api.logger.warn(`${PLUGIN_ID}: v5 memory service write outcome unknown; duplicate fallback skipped`);
        return false;
      }
      const result = serviceResult || await runAutoMemory(api, turn, assistantReply, config);
      api.logger.info(
        `${PLUGIN_ID}: auto memory trigger=${trigger} candidates=${result.candidate_count || 0} promoted=${result.promoted_count || 0} pending=${result.pending_count || 0} conflicts=${result.conflict_count || 0}`,
      );
      return result.ok === true;
    } catch (error) {
      const reason = error instanceof Error ? error.message : "unknown_error";
      api.logger.warn(`${PLUGIN_ID}: auto memory unavailable (${reason}); chat preserved`);
      return false;
    }
  };

  api.on(
    "before_prompt_build",
    async (event: any, ctx: any) => {
      const baseConfig = readConfig(api, event);
      const config = readV3Config(api, event);
      if (!baseConfig.enabled || (!config.autoMemoryEnabled && !config.multiBubbleEnabled)) {
        return;
      }
      const prompt = optionalString(event?.prompt);
      if (!prompt || ctx?.jobId) return;
      const channel = channelFromContext(ctx);
      if (!channelIsAllowed(baseConfig, channel)) return;
      const userId = resolveUserId(baseConfig, senderIdFromContext(ctx), channel);
      if (!userId) return;
      const aliases = aliasesFor(ctx, event);
      const turn = {
        userId,
        message: prompt,
        conversationKey: preferredPolicyKey(aliases),
        surface: channel === WEIXIN_CHANNEL ? "wechat" : "native",
        turnId: randomUUID(),
      };
      if (config.autoMemoryEnabled) memoryTurns.register(aliases, turn);
      if (config.multiBubbleEnabled && channel === WEIXIN_CHANNEL) {
        bubbleTurns.register(aliases, turn);
      }
    },
    { priority: 55, timeoutMs: 2000 },
  );

  api.on("agent_end", async (event: any, ctx: any) => {
    if (event?.success !== true) return;
    await commitMemory(
      "agent_end",
      aliasesFor(ctx, event),
      assistantTextFromEvent(event, ctx),
      event,
    );
  });

  api.on("message_sent", async (event: any, ctx: any) => {
    if (event?.success !== true) return;
    await commitMemory(
      "message_sent",
      aliasesFor(ctx, event),
      optionalString(event?.content),
      event,
    );
  });

  api.on(
    "message_sending",
    async (event: any, ctx: any) => {
      const config = readV3Config(api, event);
      if (!config.multiBubbleEnabled) return;
      const text = optionalString(event?.content) || optionalString(event?.text);
      const to = targetFromContext(ctx, event);
      if (!text || !to || eventHasMedia(event)) return;
      const channel = eventChannel(event, ctx);
      if (channel !== WEIXIN_CHANNEL && !to.endsWith("@im.wechat")) return;
      const accountId = accountIdFromContext(ctx, event);
      const fingerprint = bubbleFingerprint(to, accountId, text);
      if (consumeBubblePassThrough(fingerprint)) return;

      const aliases = aliasesFor(ctx, event);
      const turn = bubbleTurns.consume(aliases);
      if (!turn) return;
      const bubbles = splitNaturalBubbles(text, {
        minChars: config.multiBubbleMinChars,
        maxChars: config.multiBubbleMaxChars,
        maxBubbles: config.multiBubbleMaxBubbles,
      });
      if (bubbles.length < 2) return;

      await commitMemory("message_sending", aliases, text, event);
      const loadAdapter = api.runtime?.channel?.outbound?.loadAdapter;
      if (typeof loadAdapter !== "function") return;
      const adapter = await loadAdapter(WEIXIN_CHANNEL);
      if (typeof adapter?.sendText !== "function") return;

      let sentCount = 0;
      try {
        for (let index = 0; index < bubbles.length; index += 1) {
          const bubble = bubbles[index];
          const childFingerprint = bubbleFingerprint(to, accountId, bubble);
          const token = addBubblePassThrough(childFingerprint);
          try {
            await adapter.sendText({
              cfg: api.config,
              to,
              text: bubble,
              ...(accountId ? { accountId } : {}),
            } as any);
          } finally {
            releaseBubblePassThrough(childFingerprint, token);
          }
          sentCount += 1;
          if (index < bubbles.length - 1 && config.multiBubbleDelayMs > 0) {
            await sleep(config.multiBubbleDelayMs);
          }
        }
        api.logger.info(`${PLUGIN_ID}: multi bubble sent count=${bubbles.length}`);
        return {
          cancel: true,
          cancelReason: "yogurt_multi_bubble",
          metadata: { bubbleCount: bubbles.length },
        };
      } catch (error) {
        const reason = error instanceof Error ? error.message : "unknown_error";
        api.logger.warn(
          `${PLUGIN_ID}: multi bubble failed sent=${sentCount}/${bubbles.length} (${reason})`,
        );
        if (sentCount === 0) return;
        return {
          cancel: true,
          cancelReason: "yogurt_multi_bubble_partial",
          metadata: { bubbleCount: bubbles.length, sentCount },
        };
      }
    },
    { priority: 70, timeoutMs: 15000 },
  );

  api.logger.info(
    `${PLUGIN_ID}: V3 runtime registered automatic-memory and optional multi-bubble hooks`,
  );
}
