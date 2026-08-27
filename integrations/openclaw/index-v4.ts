import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import v3Plugin from "./index-v3.ts";
import { assistantTextFromEvent } from "./sticker-v21-utils.ts";
import {
  asRecord,
  channelFromContext,
  channelIsAllowed,
  contextAliases,
  isNativeVoiceContext,
  optionalString,
  preferredPolicyKey,
  readConfig,
  resolveUserId,
  senderIdFromContext,
} from "./sticker-plugin-utils.ts";

const PLUGIN_ID = "yogurt-social-injector";
const WEIXIN_CHANNEL = "openclaw-weixin";
const RESPONSE_PLAN_MARKER = "[Yogurt Response Plan V4.1]";
const MAX_PENDING_TURNS = 200;
const TURN_TTL_MS = 15 * 60 * 1000;
const MAX_OBSERVER_OUTPUT_BYTES = 256 * 1024;

type V4Config = {
  conversationStateEnabled: boolean;
  conversationStateTimeoutMs: number;
  conversationStateTopicTtlMinutes: number;
  conversationStateMoodTtlMinutes: number;
  conversationStateThreadTtlMinutes: number;
  conversationStateMaxOpenThreads: number;
  conversationStateMaxRecentActions: number;
  responsePlannerEnabled: boolean;
  responsePlannerMaxContextChars: number;
  memoryRecallGateEnabled: boolean;
  memoryRecallMinScore: number;
  memoryRecallCooldownTurns: number;
  memoryRecallSameMemoryCooldownTurns: number;
  memoryRecallMaxCandidates: number;
  memoryServiceEnabled: boolean;
  memoryServiceUrl: string;
  memoryServiceTimeoutMs: number;
  memoryServicePersonaId: string;
  memoryServiceRecallMaxItems: number;
  memoryServiceRecallTokenBudget: number;
};

type PendingStateTurn = {
  userId: string;
  conversationKey: string;
  keys: string[];
  createdAt: number;
};

type ObserverResult = {
  ok?: boolean;
  phase?: string;
  state?: Record<string, unknown>;
  response_plan?: Record<string, unknown> | null;
  response_plan_context?: string;
  memory_recall?: Record<string, unknown> | null;
  error?: string;
  error_type?: string;
};

type MemoryServiceRecall = {
  decision?: unknown;
  memories?: unknown;
  token_estimate?: unknown;
  reason?: unknown;
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

function readV4Config(api: any, event?: any): V4Config {
  const raw = {
    ...asRecord(api.pluginConfig),
    ...asRecord(asRecord(event?.context).pluginConfig),
  };
  return {
    conversationStateEnabled: booleanValue(raw.conversationStateEnabled, false),
    conversationStateTimeoutMs: integerValue(
      raw.conversationStateTimeoutMs,
      2500,
      500,
      10000,
    ),
    conversationStateTopicTtlMinutes: integerValue(
      raw.conversationStateTopicTtlMinutes,
      60,
      1,
      10080,
    ),
    conversationStateMoodTtlMinutes: integerValue(
      raw.conversationStateMoodTtlMinutes,
      30,
      1,
      1440,
    ),
    conversationStateThreadTtlMinutes: integerValue(
      raw.conversationStateThreadTtlMinutes,
      1440,
      1,
      43200,
    ),
    conversationStateMaxOpenThreads: integerValue(
      raw.conversationStateMaxOpenThreads,
      5,
      1,
      20,
    ),
    conversationStateMaxRecentActions: integerValue(
      raw.conversationStateMaxRecentActions,
      6,
      1,
      20,
    ),
    responsePlannerEnabled: booleanValue(raw.responsePlannerEnabled, false),
    responsePlannerMaxContextChars: integerValue(
      raw.responsePlannerMaxContextChars,
      1600,
      300,
      1800,
    ),
    memoryRecallGateEnabled: booleanValue(raw.memoryRecallGateEnabled, false),
    memoryRecallMinScore: numberValue(raw.memoryRecallMinScore, 0.48, 0.2, 0.95),
    memoryRecallCooldownTurns: integerValue(
      raw.memoryRecallCooldownTurns,
      8,
      0,
      100,
    ),
    memoryRecallSameMemoryCooldownTurns: integerValue(
      raw.memoryRecallSameMemoryCooldownTurns,
      20,
      0,
      300,
    ),
    memoryRecallMaxCandidates: integerValue(
      raw.memoryRecallMaxCandidates,
      5,
      1,
      20,
    ),
    memoryServiceEnabled: booleanValue(raw.memoryServiceEnabled, false),
    memoryServiceUrl: typeof raw.memoryServiceUrl === "string" && /^http:\/\/127\.0\.0\.1(?::\d+)?$/i.test(raw.memoryServiceUrl.trim())
      ? raw.memoryServiceUrl.trim()
      : "http://127.0.0.1:17621",
    memoryServiceTimeoutMs: integerValue(raw.memoryServiceTimeoutMs, 450, 100, 5000),
    memoryServicePersonaId: typeof raw.memoryServicePersonaId === "string" && raw.memoryServicePersonaId.trim()
      ? raw.memoryServicePersonaId.trim().slice(0, 180)
      : "yogurt",
    memoryServiceRecallMaxItems: integerValue(raw.memoryServiceRecallMaxItems, 3, 1, 3),
    memoryServiceRecallTokenBudget: integerValue(raw.memoryServiceRecallTokenBudget, 220, 0, 350),
  };
}

function v4ObserverEnabled(config: V4Config): boolean {
  return (
    config.conversationStateEnabled ||
    config.responsePlannerEnabled ||
    config.memoryRecallGateEnabled
  );
}

async function recallFromMemoryService(
  config: V4Config,
  payload: Record<string, unknown>,
): Promise<MemoryServiceRecall | undefined> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.memoryServiceTimeoutMs);
  try {
    const response = await fetch(`${config.memoryServiceUrl}/v1/memory/recall`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok) return undefined;
    const value: unknown = await response.json();
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as MemoryServiceRecall
      : undefined;
  } catch {
    return undefined;
  } finally {
    clearTimeout(timer);
  }
}

function compactMemoryServiceLog(result: MemoryServiceRecall): string {
  const memories = Array.isArray(result.memories) ? result.memories : [];
  const first = asRecord(memories[0]);
  return [
    `decision=${optionalString(result.decision) || "none"}`,
    `kind=${optionalString(first.kind) || "none"}`,
    `sourceId=${typeof first.source_id === "number" ? first.source_id : 0}`,
    `tokens=${typeof result.token_estimate === "number" ? result.token_estimate : 0}`,
  ].join(" ");
}

function eventMetadata(event: any): Record<string, unknown> {
  return asRecord(event?.metadata);
}

function accountIdFromContext(event: any, ctx: any): string | undefined {
  const metadata = eventMetadata(event);
  return (
    optionalString(event?.accountId) ||
    optionalString(metadata.accountId) ||
    optionalString(ctx?.accountId) ||
    optionalString(ctx?.channelContext?.accountId) ||
    optionalString(ctx?.deliveryContext?.accountId)
  );
}

function targetFromContext(event: any, ctx: any): string | undefined {
  return (
    optionalString(event?.to) ||
    optionalString(ctx?.to) ||
    optionalString(ctx?.chatId) ||
    optionalString(ctx?.conversationId) ||
    optionalString(ctx?.channelContext?.chat?.id) ||
    optionalString(ctx?.deliveryContext?.to) ||
    senderIdFromContext(ctx)
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

function contextKeys(event: any, ctx: any): string[] {
  const accountId = accountIdFromContext(event, ctx);
  const values = [
    event?.to,
    event?.conversationId,
    event?.sessionKey,
    ctx?.senderId,
    ctx?.requesterSenderId,
    ctx?.to,
    ctx?.chatId,
    ctx?.conversationId,
    ctx?.sessionKey,
    ctx?.nativeChannelId,
    ctx?.channelContext?.chat?.id,
    ctx?.deliveryContext?.to,
    ctx?.deliveryContext?.conversationId,
    ctx?.deliveryContext?.chatId,
    senderIdFromContext(ctx),
  ]
    .map(optionalString)
    .filter((value): value is string => Boolean(value));
  const keys: string[] = [];
  for (const value of [...new Set(values)]) {
    keys.push(`value:${value}`);
    if (accountId) keys.push(`account:${accountId}:${value}`);
  }
  return [...new Set(keys)];
}

class PendingStateTurnStore {
  private readonly byKey = new Map<string, PendingStateTurn>();

  register(entry: Omit<PendingStateTurn, "createdAt">): void {
    this.prune();
    const keys = [...new Set(entry.keys)].filter(Boolean);
    if (keys.length === 0) return;
    const value: PendingStateTurn = {
      ...entry,
      keys,
      createdAt: Date.now(),
    };
    for (const key of keys) this.byKey.set(key, value);
    if (this.byKey.size > MAX_PENDING_TURNS) this.prune(true);
  }

  consume(event: any, ctx: any): PendingStateTurn | undefined {
    this.prune();
    for (const key of contextKeys(event, ctx)) {
      const entry = this.byKey.get(key);
      if (!entry) continue;
      this.delete(entry);
      return entry;
    }
    return undefined;
  }

  private delete(entry: PendingStateTurn): void {
    for (const key of entry.keys) {
      if (this.byKey.get(key) === entry) this.byKey.delete(key);
    }
  }

  private prune(force = false): void {
    const cutoff = Date.now() - TURN_TTL_MS;
    const values = [...new Set(this.byKey.values())].sort(
      (left, right) => left.createdAt - right.createdAt,
    );
    for (const entry of values) {
      if (!force && entry.createdAt >= cutoff) continue;
      this.delete(entry);
      if (force && this.byKey.size <= MAX_PENDING_TURNS / 2) break;
    }
  }
}

function runObserver(
  api: any,
  event: any,
  request: Record<string, unknown>,
): Promise<ObserverResult> {
  const baseConfig = readConfig(api, event);
  const config = readV4Config(api, event);
  return new Promise((resolvePromise, rejectPromise) => {
    const pythonSource = join(baseConfig.repoPath, "social_layer", "src");
    if (!existsSync(pythonSource)) {
      rejectPromise(new Error("conversation_state_python_source_missing"));
      return;
    }
    const args = [
      "-m",
      "yogurt_social.conversation_state_observer",
      "--db",
      baseConfig.dbPath,
      "--topic-ttl-minutes",
      String(config.conversationStateTopicTtlMinutes),
      "--mood-ttl-minutes",
      String(config.conversationStateMoodTtlMinutes),
      "--thread-ttl-minutes",
      String(config.conversationStateThreadTtlMinutes),
      "--max-open-threads",
      String(config.conversationStateMaxOpenThreads),
      "--max-recent-actions",
      String(config.conversationStateMaxRecentActions),
      "--memory-recall-min-score",
      String(config.memoryRecallMinScore),
      "--memory-recall-cooldown-turns",
      String(config.memoryRecallCooldownTurns),
      "--memory-recall-same-memory-cooldown-turns",
      String(config.memoryRecallSameMemoryCooldownTurns),
      "--memory-recall-max-candidates",
      String(config.memoryRecallMaxCandidates),
    ];
    const child = spawn(baseConfig.pythonPath, args, {
      cwd: baseConfig.repoPath,
      env: {
        ...process.env,
        PYTHONPATH: process.env.PYTHONPATH
          ? `${pythonSource}${delimiter}${process.env.PYTHONPATH}`
          : pythonSource,
        YOGURT_SOCIAL_DB: baseConfig.dbPath,
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
      rejectOnce("conversation_state_timeout");
    }, config.conversationStateTimeoutMs);

    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
      if (Buffer.byteLength(stdout, "utf8") > MAX_OBSERVER_OUTPUT_BYTES) {
        child.kill("SIGKILL");
        rejectOnce("conversation_state_output_too_large");
      }
    });
    child.stderr.on("data", () => {});
    child.on("error", () => rejectOnce("conversation_state_spawn_failed"));
    child.on("close", (code) => {
      clearTimeout(timer);
      if (settled) return;
      if (code !== 0) {
        rejectOnce(`conversation_state_exit_${String(code)}`);
        return;
      }
      try {
        settled = true;
        resolvePromise(JSON.parse(stdout) as ObserverResult);
      } catch {
        rejectOnce("conversation_state_invalid_json");
      }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

function compactStateLog(result: ObserverResult): string {
  const state = asRecord(result.state);
  const threads = Array.isArray(state.open_threads) ? state.open_threads.length : 0;
  return [
    `topic=${optionalString(state.current_topic) || "none"}`,
    `act=${optionalString(state.dialogue_act) || "none"}`,
    `mood=${optionalString(state.user_mood) || "neutral"}`,
    `interaction=${optionalString(state.interaction_mode) || "casual"}`,
    `threads=${threads}`,
    `pattern=${optionalString(state.last_response_pattern) || "none"}`,
    `version=${typeof state.state_version === "number" ? state.state_version : 0}`,
  ].join(" ");
}

function compactPlanLog(result: ObserverResult): string {
  const plan = asRecord(result.response_plan);
  return [
    `act=${optionalString(plan.dialogue_act) || "none"}`,
    `goal=${optionalString(plan.primary_goal) || "none"}`,
    `length=${optionalString(plan.reply_length) || "none"}`,
    `bubbles=${typeof plan.bubble_target === "number" ? plan.bubble_target : 0}`,
    `followup=${plan.ask_followup === true}`,
    `sticker=${optionalString(plan.sticker_preference) || "neutral"}`,
    `stateVersion=${typeof plan.state_version === "number" ? plan.state_version : 0}`,
  ].join(" ");
}

function compactRecallLog(result: ObserverResult): string {
  const recall = asRecord(result.memory_recall);
  const candidate = asRecord(recall.candidate);
  const reasons = Array.isArray(recall.reasons)
    ? recall.reasons.filter((item) => typeof item === "string").slice(0, 8)
    : [];
  return [
    `decision=${optionalString(recall.decision) || "none"}`,
    `layer=${optionalString(candidate.layer) || "none"}`,
    `kind=${optionalString(candidate.kind) || "none"}`,
    `sourceId=${typeof candidate.source_id === "number" ? candidate.source_id : 0}`,
    `score=${typeof candidate.score === "number" ? candidate.score.toFixed(3) : "0.000"}`,
    `threshold=${typeof recall.threshold === "number" ? recall.threshold.toFixed(3) : "0.000"}`,
    `explicit=${recall.explicit_request === true}`,
    `cooldown=${typeof recall.cooldown_remaining === "number" ? recall.cooldown_remaining : 0}`,
    `sameCooldown=${typeof recall.same_memory_cooldown_remaining === "number" ? recall.same_memory_cooldown_remaining : 0}`,
    `turn=${typeof recall.turn_index === "number" ? recall.turn_index : 0}`,
    `reasons=${reasons.join(",") || "none"}`,
  ].join(" ");
}

function responsePlanContext(
  result: ObserverResult,
  config: V4Config,
): string | undefined {
  const value = optionalString(result.response_plan_context);
  if (!value || !value.startsWith(RESPONSE_PLAN_MARKER)) return undefined;
  return value.slice(0, config.responsePlannerMaxContextChars);
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Yogurt Social Injector",
  description:
    "Injects Yogurt social context, V2.1 stickers, V3 memory/multi-bubble, V4 state, V4.1 planning, and opt-in V4.2 shadow recall gating.",
  register(api: any) {
    const pendingTurns = new PendingStateTurnStore();
    (v3Plugin as any).register(api);

    api.on(
      "before_prompt_build",
      async (event: any, ctx: any) => {
        if (isNativeVoiceContext(ctx, event)) return;
        const baseConfig = readConfig(api, event);
        const config = readV4Config(api, event);
        const observerEnabled = v4ObserverEnabled(config);
        if (!baseConfig.enabled || (!observerEnabled && !config.memoryServiceEnabled)) return;
        const prompt = optionalString(event?.prompt);
        if (!prompt || ctx?.jobId) return;
        const channel = channelFromContext(ctx);
        if (!channelIsAllowed(baseConfig, channel)) return;
        const senderId = senderIdFromContext(ctx);
        const userId = resolveUserId(baseConfig, senderId, channel);
        if (!userId) return;
        const aliases = contextAliases(ctx, channel, senderId, event);
        const conversationKey = preferredPolicyKey(aliases);

        try {
          let result: ObserverResult | undefined;
          if (observerEnabled) {
            result = await runObserver(api, event, {
              user_id: userId,
              conversation_key: conversationKey,
              phase: "user",
              message: prompt,
              include_response_plan: config.responsePlannerEnabled,
              include_memory_recall: config.memoryRecallGateEnabled,
            });
            pendingTurns.register({
              userId,
              conversationKey,
              keys: contextKeys(event, ctx),
            });
            api.logger.info(
              `${PLUGIN_ID}: v4 state observed phase=user ${compactStateLog(result)}`,
            );
            if (config.memoryRecallGateEnabled) {
              api.logger.info(
                `${PLUGIN_ID}: v4 memory recall shadow ${compactRecallLog(result)}`,
              );
            }
          }
          const contexts: string[] = [];
          if (config.responsePlannerEnabled && result) {
            const planContext = responsePlanContext(result, config);
            if (!planContext) {
              api.logger.warn(
                `${PLUGIN_ID}: v4 response plan unavailable (missing_or_invalid_context); chat preserved`,
              );
            } else {
              api.logger.info(
                `${PLUGIN_ID}: v4 response plan ready ${compactPlanLog(result)} contextChars=${planContext.length}`,
              );
              contexts.push(planContext);
            }
          }
          if (config.memoryServiceEnabled) {
            const memoryResult = await recallFromMemoryService(config, {
              persona_id: config.memoryServicePersonaId,
              user_id: userId,
              surface: "wechat",
              conversation_key: conversationKey,
              message: prompt,
              max_items: config.memoryServiceRecallMaxItems,
              max_tokens: config.memoryServiceRecallTokenBudget,
            });
            if (!memoryResult) {
              api.logger.warn(`${PLUGIN_ID}: v5 memory service unavailable; chat preserved`);
            } else {
              api.logger.info(
                `${PLUGIN_ID}: v5 memory recall shadow ${compactMemoryServiceLog(memoryResult)}`,
              );
            }
          }
          if (contexts.length === 0) return;
          return { prependContext: contexts.join("\n\n") };
        } catch (error) {
          const reason = error instanceof Error ? error.message : "unknown_error";
          api.logger.warn(
            `${PLUGIN_ID}: v4 state unavailable phase=user (${reason}); chat preserved`,
          );
          return;
        }
      },
      { priority: 50, timeoutMs: 12000 },
    );

    const observeAssistant = async (event: any, ctx: any, text: string) => {
      const config = readV4Config(api, event);
      if (!v4ObserverEnabled(config)) return;
      const pending = pendingTurns.consume(event, ctx);
      if (!pending) return;
      try {
        const result = await runObserver(api, event, {
          user_id: pending.userId,
          conversation_key: pending.conversationKey,
          phase: "assistant",
          assistant_reply: text,
        });
        api.logger.info(
          `${PLUGIN_ID}: v4 state observed phase=assistant ${compactStateLog(result)}`,
        );
      } catch (error) {
        const reason = error instanceof Error ? error.message : "unknown_error";
        api.logger.warn(
          `${PLUGIN_ID}: v4 state unavailable phase=assistant (${reason}); chat preserved`,
        );
      }
    };

    api.on(
      "message_sending",
      async (event: any, ctx: any) => {
        if (eventHasMedia(event)) return;
        const text = optionalString(event?.content) || optionalString(event?.text);
        const target = targetFromContext(event, ctx);
        if (!text || !target) return;
        const channel =
          optionalString(event?.channel) ||
          optionalString(eventMetadata(event).channel) ||
          channelFromContext(ctx);
        if (channel !== WEIXIN_CHANNEL && !target.endsWith("@im.wechat")) return;
        await observeAssistant(event, ctx, text);
      },
      { priority: 75, timeoutMs: 12000 },
    );

    api.on("agent_end", async (event: any, ctx: any) => {
      if (event?.success !== true) return;
      const text = assistantTextFromEvent(event, ctx);
      if (!text) return;
      await observeAssistant(event, ctx, text);
    });

    api.logger.info(
      `${PLUGIN_ID}: V4 state, V4.1 planner, and optional V4.2 shadow recall gate registered`,
    );
  },
});
