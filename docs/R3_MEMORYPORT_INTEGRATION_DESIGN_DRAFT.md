# Yogurt R3 MemoryPort Integration Design Draft v0.1

**Status:** PROPOSED / DESIGN ONLY  
**Implementation status:** NOT STARTED  
**Depends on:** R1 Contract Foundation + R2 Core Foundation  
**Primary objective:** 将现有 Yogurt Memory V5 作为可替换 capability，通过 typed ports / adapters 接入 Yogurt Core，同时保持 Core provider-neutral、保持 R2 durability/authority 语义，并且不污染当前 Memory B.7/B.8 评估。

---

## 1. 背景

R2 已经完成并封板以下 Core Foundation：

```text
R1 Contract Foundation        PASS / MERGED
R2-A Ephemeral Core           PASS / MERGED
R2-B Durable Persistence      PASS / MERGED
R2-C Recovery / Lifecycle     PASS / MERGED
R2-D Operation Engine         PASS / MERGED
R2-E Final Conformance Gate   PASS / MERGED
```

R3 不再设计第二套 durability、authority、recovery 或 operation semantics。

R3 的任务是第一次把一个真实的既有能力模块接到 Core，验证：

```text
Core Foundation
      +
existing capability
      +
typed provider boundary
      +
failure isolation
      =
可长期演进的 Yogurt Runtime
```

选择 Memory 作为第一个 capability，是因为 Memory 已经存在独立 V5 Service、shadow/gate 评估体系、稳定数据与 idempotency 基础，适合验证 Core 的 port / adapter 设计。

---

## 2. 当前 Memory V5 现实基线

R3 不重写 Memory V5。

当前服务位于：

```text
social_layer/src/yogurt_social/memory_service/
```

默认本地地址：

```text
http://127.0.0.1:17621
```

当前 HTTP API：

```text
GET  /v1/health
GET  /v1/ready
POST /v1/memory/recall
POST /v1/memory/observe
GET  /v1/memory/snapshot
GET  /v1/memory/candidates
GET  /v1/memory/revisions
```

当前 `RecallRequest` 主要字段：

```text
persona_id
user_id
surface
conversation_key
message
max_tokens
max_items
turn_id
```

当前 `ObserveRequest` 主要字段：

```text
persona_id
user_id
surface
conversation_key
phase = user | assistant
user_message
assistant_reply
idempotency_key
```

当前 V5 recall：

- top-1 为主；
- 有 pre-gate；
- 有 recall score / continuity / quality；
- 可返回 allow / skip / blocked；
- 带 B.7 gate experiment audit；
- recall candidate 当前不应自动等价为 Core model context；
- 当前 production recall injection gate 与 R3 是两个不同概念。

当前 observe：

- `phase=user` 更新 conversation state；
- `phase=assistant` 更新 conversation state、运行 auto-memory，并尝试 rebuild derived index；
- 支持 `idempotency_key`；
- canonical memory write 与 derived index write 已经区分。

这些行为是 **adapter target 的现实**，不是未来 Core contract 本身。

---

## 3. R3 核心原则

### 3.1 Core 不依赖 Memory V5 实现

禁止：

```text
Yogurt Core
  → import social_layer
  → import yogurt_social.memory_service
  → import MemoryStore / AutoMemoryStore / FTS implementation
```

允许：

```text
Yogurt Core
  → Memory ports / contracts

composition root / integration package
  → Memory V5 Adapter
  → existing Memory Service
```

### 3.2 Memory 是 optional capability

Memory 不可用时：

```text
Core basic Turn execution MUST continue
```

R3 不允许形成：

```text
Memory unavailable
→ Core NOT_SERVING
```

除非未来某个明确场景声明 Memory 为 required capability；R3 默认不是。

### 3.3 Memory 不直接修改 prompt

Memory 返回 normalized recall result。

只有 Core 的 ContextComposer / policy 可以决定是否进入 model context。

禁止：

```text
Memory Adapter
→ mutate model prompt
```

目标：

```text
MemoryRecallPort
      ↓
normalized recall result
      ↓
Core Context policy
      ↓
ContextProjection
      ↓
ModelPort
```

### 3.4 Durable Memory write 必须复用 R2-D

任何可能造成 durable Memory domain write 的 observation：

```text
logical memory observation
↓
durable OperationIntentCommitted
↓
commit confirmed
↓
Memory provider dispatch
↓
durable operation outcome
```

不能直接：

```text
Core → HTTP POST /observe
```

绕过 Operation Engine。

### 3.5 不承诺 universal exactly-once

Memory observation 使用 stable OperationId / provider idempotency key。

目标是：

```text
same logical observation
→ same OperationId
→ same provider idempotency key
```

而不是宣称跨进程、跨数据库 universal exactly-once。

---

## 4. 一个重要现实差异：当前 `/recall` 不是严格纯读

当前 Memory V5 `recall()` 在执行过程中可能：

1. rebuild derived FTS index；
2. 如果当前 Turn 尚未执行 user observe，则调用 `observe_user_turn()`。

因此 R3 **不能在 contract 层撒谎，把现有 `/recall` 无条件称为 PURE / READ_ONLY**。

### R3 的解决策略

在真实 V5 integration 中，正常顺序应为：

```text
UserContentObservation
  → durable operation
  → V5 /observe phase=user
  → same TurnId 已建立 observation evidence

then

MemoryRecallPort.query(... same TurnId ...)
  → V5 /recall
```

这样 current recall 可以检测已经完成的 user observation，不需要再次 canonical `observe_user_turn()`。

FTS rebuild 属于 Memory provider 内部 derived-index maintenance，不作为 Core authoritative semantic write；canonical Memory domain write 仍必须通过 observation operation。

### R3-A 限制

在 R3-B durable user observation 尚未接通前：

```text
R3-A MUST NOT 对 production Memory DB 直接运行“看似 read-only”的真实 recall shadow
```

R3-A 只做 typed port + fake/in-memory contract tests。

真实 V5 recall integration 放到 R3-C，并要求 user observation ordering 已经成立。

---

## 5. Target architecture

```text
                         Yogurt Core
                              │
             ┌────────────────┴────────────────┐
             │                                 │
      MemoryRecallPort                  Operation Engine
        READ boundary                         │
             │                          Memory Observation
             │                          logical operations
             │                                 │
             └──────────────┬──────────────────┘
                            │
                            ▼
                 Memory V5 Integration Adapter
                    integrations/memory_v5
                            │
                            ▼
                  Existing Memory V5 Service
                    127.0.0.1:17621
                            │
                            ▼
                   existing Memory database
```

### Package rule

Memory V5 adapter **不放入** `yogurt_core_adapters`。

原因：R2-E 已经把 `yogurt_core` / `yogurt_core_adapters` 的 provider dependency boundary 锁死。

建议：

```text
integrations/memory_v5/
  __init__.py
  recall_adapter.py
  operation_provider.py
  mapping.py
  tests/
```

依赖方向：

```text
integrations.memory_v5
  → yogurt_core ports
  → yogurt_contracts
  → existing MemoryServiceClient / HTTP protocol

Yogurt Core
  ✗→ integrations.memory_v5
  ✗→ social_layer
```

Composition root 在 Core 外完成 wiring。

---

## 6. Port split

R3 不建议做一个模糊的：

```text
MemoryPort.read_write_everything()
```

而是区分 read 与 side effect。

### 6.1 MemoryRecallPort

职责：

```text
query relevant memory
return normalized provider-neutral evidence
```

概念接口：

```python
class MemoryRecallPort(Protocol):
    async def recall(self, query: MemoryRecallQuery) -> MemoryRecallResult:
        ...
```

它不是 Memory policy owner。

### 6.2 Memory observation write boundary

User / assistant / presentation observations不直接通过普通 `MemoryRecallPort` 写入。

它们转换成 R2-D `LogicalOperation`，通过 Operation Engine dispatch 到一个 provider-neutral operation provider。

概念：

```text
Core semantic event
↓
Memory Observation Mapper
↓
LogicalOperation(target_capability = memory.observe.*)
↓
OperationEngine
↓
MemoryObservationProvider
↓
Memory V5 Adapter
```

这样 durability 不需要在 R3 重新实现。

---

## 7. Recall contracts

如果 R1 现有 contracts 已包含足够的 memory read types，应优先复用。

只有缺口才新增 R3-specific read contract。

建议逻辑模型：

```text
MemoryRecallQuery
  conversation_ref
  turn_ref / turn_id
  turn_origin
  normalized user message
  max_items
  token_budget
  policy_mode
```

Core contract 不携带：

```text
Memory SQLite path
FTS query syntax
V5 score implementation details
OpenClaw session key
HTTP URL
social_layer object
```

### 7.1 MemoryRecallResult

建议：

```text
MemoryRecallResult
  status
  candidates[]
  provider_metadata_summary
  token_estimate
```

其中 status 至少能表达：

```text
AVAILABLE
EMPTY
SKIPPED
BLOCKED
UNAVAILABLE
INVALID_PROVIDER_RESPONSE
```

Core 不应该依赖 V5 当前字符串：

```text
allow
skip
blocked
shadow_candidate
gate_allow
```

这些由 adapter 转换。

### 7.2 Recall candidate

至少包含：

```text
MemoryRecallCandidate
  stable candidate identity
  kind
  normalized text
  score/evidence if policy permits
  source reference
```

不能返回：

```text
raw SQLite row
provider object
MemoryStore instance
```

---

## 8. Observation contracts

R0.5 已冻结三类逻辑 observation：

```text
UserContentObservation
CommittedContentObservation
PresentationObservation
```

R3 应复用这些 semantic boundaries，而不是把 Core contract 简化成：

```text
phase=user|assistant
```

因为 `phase=user|assistant` 是当前 Memory V5 HTTP compatibility schema，不是长期 Runtime contract。

### 8.1 UserContentObservation

来源：

```text
InputCommitted
```

必须只包含 committed / normalized user input。

不得包含：

```text
raw credentials
provider reasoning
uncommitted STT partial
```

### 8.2 CommittedContentObservation

来源：

```text
AgentOutputSegmentCommitted
or explicitly defined finalized committed content
```

不得发送 draft model output。

### 8.3 PresentationObservation

来源：

```text
PresentationReceipt / presentation projection evidence
```

用于表达：

```text
zero presentation
partial extent
presented/delivered/interrupted/failed evidence
```

它与 `CommittedContentObservation` 是不同 logical operation，必须使用不同 OperationId。

---

## 9. Memory V5 compatibility gap

当前 `/v1/memory/observe` 只有：

```text
phase=user
phase=assistant
```

当前 schema 没有原生 `PresentationObservation` 类型。

因此 R3 不允许简单声称：

```text
R0.5 Memory observation semantics fully integrated
```

而实际只把 presentation 丢掉。

### v0.1 决策

R3 分两层处理：

1. Core contracts 从一开始保持三类 observation 正确语义；
2. Memory V5 adapter 只映射它能够安全表达的能力；
3. unsupported observation 必须显式 capability-unavailable / not-dispatched，而不是 silently drop；
4. 如果后续需要给 Memory V5 增加小型 compatibility endpoint/schema，可以做 provider-side additive extension，但不得把 provider schema反向泄露到 Core。

建议 Memory V5 additive extension 方向：

```text
POST /v1/memory/observations

kind:
  user_content
  committed_content
  presentation

operation_id / idempotency_key
correlation ids
normalized payload
```

这只是候选方案，需在 R3 implementation review 前单独确认；v0.1 不把它视为已实现事实。

---

## 10. Mapping: Core identity → Memory V5

Core authoritative identity：

```text
ConversationRef {
  persona_id
  user_id
  conversation_id
}
```

Memory V5 当前需要：

```text
persona_id
user_id
surface
conversation_key
```

adapter mapping：

```text
persona_id       ← ConversationRef.persona_id
user_id          ← ConversationRef.user_id
conversation_key ← ConversationRef.conversation_id
surface          ← TurnOrigin.origin_surface
```

Memory V5 自己负责当前的 surface scoping，例如：

```text
surface:conversation_key
```

Core 的 `conversation_id` MUST NOT 因 Memory provider 要求而改成 surface-encoded ID。

### TurnId 与 OperationId

建议：

```text
V5 recall.turn_id
  ← Core TurnId

V5 observe.idempotency_key
  ← Core OperationId
```

TurnId 用于关联当前 Turn；OperationId 用于 side-effect idempotency。

两者不得混为一个概念。

---

## 11. Authorization / capability

R3 延续 R2-D：

Memory provider 不是“因为是 localhost 就自动可信”。

Memory observation operation 仍携带 immutable `OperationAuthorizationSnapshot`。

至少定义 capability IDs，例如：

```text
memory.recall
memory.observe.user_content
memory.observe.committed_content
memory.observe.presentation
```

实际命名需与 capability contract 现有 convention 对齐。

provider capability mismatch：

```text
→ NOT_DISPATCHED(CAPABILITY_UNAVAILABLE)
→ provider calls = 0
```

Memory adapter 不拥有授权策略；它只验证/消费 Core 已冻结的 authority evidence。

---

## 12. Side-effect classification

### Recall

目标分类：

```text
READ_ONLY
```

前提：R3-C real path 已通过 durable user observation ordering，recall 不再承担 canonical current-turn observation write。

### UserContentObservation

```text
IDEMPOTENT_WRITE
```

stable OperationId → V5 idempotency key。

### CommittedContentObservation

```text
IDEMPOTENT_WRITE
```

### PresentationObservation

```text
IDEMPOTENT_WRITE
```

这些 observation 都不属于 universal exactly-once。

---

## 13. Ordering

### 13.1 Turn beginning

目标：

```text
InputCommitted durable
↓
UserContentObservation logical operation created
↓
OperationIntentCommitted durable
↓
Memory observe dispatch
↓
Memory operation outcome durable
↓
MemoryRecallPort query
↓
normalized recall result
```

R3 不要求 Memory write 完成成为 Turn admission 的 blocking prerequisite。

如果 Memory Service unavailable：

```text
InputCommitted remains valid
Turn can continue
recall becomes unavailable/empty
```

### 13.2 Assistant semantic output

```text
SegmentCommitted durable
↓
CommittedContentObservation operation MAY be created
↓
Operation Engine
↓
Memory provider
```

不能在 `SegmentCommitted` 之前发送 assistant content。

### 13.3 Presentation

```text
PresentationReceipt durable/evidence available
↓
PresentationObservation logical operation
↓
Operation Engine
↓
Memory provider
```

presentation observation 不修改原 committed-content operation。

---

## 14. Recall → Context integration

R3 必须区分：

```text
Memory query
Memory candidate evaluation
Memory context injection
```

三者不是同一状态。

### R3-C shadow

```text
MemoryRecallPort query
↓
normalized result
↓
audit / evaluation only
↓
NO ContextProjection mutation
↓
NO model prompt injection
```

### R3-D production integration

只有 gate 被明确授权后：

```text
MemoryRecallResult
↓
Core Memory Context Policy
↓
ContextComposer
↓
ContextProjection
↓
ModelPort
```

Memory adapter 永远不能直接：

```text
append system prompt
inject provider-specific text block
```

---

## 15. B.7 / B.8 coexistence — HARD CONSTRAINT

当前 Memory gate 仍有自然流量 shadow / sampling 工作。

R3 不允许为了 integration test 污染这批统计。

### 15.1 当前允许立即开始

```text
R3 Design
R3-A contracts
R3-A fake MemoryRecallPort
R3-B operation mapping
R3-B fake Memory provider
R3 adapter unit tests against isolated fixtures
```

### 15.2 当前禁止对 production sampling DB 做的事情

在 B.7/B.8 未结束或没有明确 audit-source exclusion 前：

```text
DO NOT run R3 real recall shadow against production natural-sampling path
DO NOT create synthetic R3 turns in production B.7 audit stream
DO NOT enable recall context injection
DO NOT change production recall threshold/gate configuration
```

### 15.3 R3-C 解锁条件

二选一：

```text
A. B.7/B.8 sampling completed
```

或：

```text
B. Memory Service 能明确标记 source=r3_core_shadow，
   并且该 source 被实验统计确定性排除
```

否则 R3-C 只能使用：

```text
isolated copied DB
sandbox DB
fixture Memory Service
```

---

## 16. Timeout and fail-open semantics

Memory 是 optional capability。

### Recall timeout

```text
Memory Service timeout
→ MemoryRecallResult.UNAVAILABLE
→ no memory injected
→ Turn continues
```

不得：

```text
Memory timeout
→ abort whole Turn
```

### Observe provider unavailable before dispatch

```text
→ NOT_DISPATCHED(PROVIDER_NOT_AVAILABLE)
→ Core semantic fact remains committed
```

### Observe provider timeout after dispatch

如果 outcome 无法确定：

```text
→ OUTCOME_UNKNOWN
```

不能自动标 FAILED。

### Retry

Memory observation 是 KEYED idempotent write 时：

```text
controlled retry/reconcile
→ SAME OperationId
```

不能创建新 OperationId 假装是新 observation。

---

## 17. Current MemoryServiceClient handling

现有 `MemoryServiceClient` 是一个小型 fail-open HTTP client：

```text
network / JSON / URL error
→ None
```

R3 adapter 不应把 `None` 直接泄露给 Core。

### recall adapter mapping

```text
None
→ MemoryRecallResult.UNAVAILABLE
```

### observation provider mapping

```text
None after dispatch
→ provider outcome uncertain
→ OUTCOME_UNKNOWN
```

区别非常重要：

```text
read unavailable
≠
write definitely failed
```

---

## 18. Provider response validation

Adapter 必须 fail closed。

Recall response：

- unknown decision；
- malformed memories；
- invalid candidate identity；
- unbounded text；
- invalid score / token estimate；

不得进入 ContextComposer。

Observation response：

- `ok=true` 但 contract fields mutually inconsistent；
- `in_progress=true`；
- malformed idempotency state；

不得随意映射成 `SUCCESS`。

如果 provider 已经可能执行 durable write、但结果不可证明：

```text
OUTCOME_UNKNOWN
```

---

## 19. Memory Service readiness

R3 adapter 可以使用：

```text
/v1/health
/v1/ready
```

但 readiness 只影响 capability availability。

禁止：

```text
Memory not ready
→ Core lifecycle NOT_SERVING
```

应该：

```text
Memory capability degraded/unavailable
Core remains serving
```

---

## 20. Core restart semantics

R2-C 保持不变。

Core restart：

- 不恢复旧 model execution；
- 不因 pending Memory operation 阻塞 local recovery；
- `DISPATCH_ATTEMPTED` Memory observation 不自动假设失败；
- idempotent observation 后续只通过 same OperationId controlled reconciliation；
- Memory provider unavailable 不阻止 Core SERVING。

---

## 21. Data privacy boundary

Core → Memory 只允许 normalized semantic observations。

禁止：

```text
raw model token stream
reasoning
raw provider JSON
uncommitted draft
raw audio
STT partial hypotheses
credentials
OpenClaw internal session object
absolute local model paths
```

Memory recall candidate 进入 Core 后仍需要 policy/budget validation。

---

## 22. R3 phases

### R3-A — Memory Read Contract Foundation

目标：

- typed `MemoryRecallPort`；
- provider-neutral query/result/candidate；
- fake adapter；
- timeout/unavailable/malformed response semantics；
- package absence / import boundary tests；
- **不访问 production Memory DB**；
- **不注入 model context**。

Gate：

```text
R3-A contracts/conformance PASS
```

### R3-B — Durable Memory Observation Operations

目标：

- map `UserContentObservation` to durable logical operation；
- map committed/presentation observations at contract level；
- fake Memory provider；
- `OperationIntentCommitted` before dispatch；
- same OperationId retry/reconcile；
- provider unavailable / unknown semantics；
- no Turn rollback on Memory outage。

优先完成：

```text
UserContentObservation
```

因为它是 real recall ordering 的 prerequisite。

### R3-C — Memory V5 Adapter + Shadow Integration

目标：

- `integrations/memory_v5` adapter；
- map Core identity → V5 request；
- user observe uses OperationId as V5 idempotency key；
- recall uses same TurnId correlation；
- verify user-observe-before-recall ordering；
- real results only collected/evaluated；
- **NO prompt injection**。

Hard blocker：

```text
B.7/B.8 contamination constraint
```

真实 production DB shadow 只有在 §15 解锁后允许。

### R3-D — Recall Context Integration

目标：

```text
MemoryRecallResult
→ Core memory context policy
→ ContextComposer
→ ContextProjection
→ ModelPort
```

必须可 feature-disable。

Hard blocker：

```text
Memory recall production gate explicit GO
```

没有 GO 时：

```text
R3-D remains BLOCKED
```

### R3-E — Final Conformance Gate

证明：

- Memory package physically absent → Core import succeeds；
- Memory Service unavailable → Core Turn works；
- malformed recall → never injected；
- write intent missing → no provider call；
- write ack uncertainty → same OperationId reconcile；
- restart → no blind observation redrive；
- projection/context does not silently assume memory availability；
- Memory adapter cannot mutate prompt；
- Memory backend does not become Core dependency；
- Python 3.9–3.12 CI；
- existing Memory/Voice/WeChat production-safe regression green。

---

## 23. Suggested implementation layout

仅为 design target，不是代码已存在声明：

```text
yogurt_contracts/
  memory/
    recall.py                  # only if R1 contracts lack these types


yogurt_core/
  ports/
    memory.py                  # MemoryRecallPort
  memory/
    policy.py                  # Core-owned context admission policy
    observation_mapper.py      # semantic fact → logical operation

integrations/
  memory_v5/
    __init__.py
    mapping.py
    recall_adapter.py
    operation_provider.py


tests/
  contracts/
  conformance/
  architecture/
  core/

integrations/memory_v5/tests/
```

不要把 provider-specific HTTP schema 放入 `yogurt_contracts`。

---

## 24. Required R3-A tests

至少：

1. valid recall query → normalized result；
2. empty result → EMPTY；
3. provider unavailable → UNAVAILABLE；
4. timeout → UNAVAILABLE；
5. malformed provider result → INVALID_PROVIDER_RESPONSE；
6. provider text over contract bound → reject；
7. raw V5 dict never reaches Core context；
8. optional Memory package absent → Core import succeeds；
9. MemoryRecallPort fake removable；
10. no production DB access in R3-A suite。

---

## 25. Required R3-B tests

至少：

1. no durable intent → no Memory provider dispatch；
2. committed intent → dispatch；
3. UserContentObservation uses stable OperationId；
4. provider idempotency key == stable OperationId mapping；
5. same logical observation retry → same ID；
6. conflicting OperationId fingerprint → conflict；
7. provider unavailable → NOT_DISPATCHED；
8. provider timeout after call → OUTCOME_UNKNOWN；
9. success → SUCCEEDED；
10. ack loss after provider success → durable reconcile, provider semantic effect once；
11. Core restart does not blindly redrive observation；
12. Memory failure does not abort committed Turn；
13. Ephemeral Core cannot dispatch Memory durable observation。

---

## 26. Required R3-C tests

至少：

1. Core ConversationRef mapping is deterministic；
2. surface comes from frozen TurnOrigin；
3. TurnId and OperationId are not conflated；
4. user observation occurs before recall；
5. same TurnId prevents duplicate current-turn canonical observation；
6. V5 allow → normalized AVAILABLE candidate；
7. V5 skip → SKIPPED/EMPTY；
8. V5 blocked → BLOCKED；
9. V5 malformed → invalid/fail-open；
10. Memory service down → Core continues；
11. shadow result cannot enter ContextProjection；
12. R3 audit traffic is isolated from production B.7/B.8 evidence。

---

## 27. Required R3-D tests

只有 R3-D 解锁后：

1. disabled gate → no memory context；
2. shadow mode → no memory context；
3. allowed candidate → ContextComposer receives normalized candidate only；
4. token budget exceeded → no injection；
5. malformed/unavailable result → no injection；
6. context ordering deterministic；
7. current user input not duplicated；
8. Memory adapter cannot directly mutate model prompt；
9. same Turn frozen recall/context worldview；
10. Memory result from another ConversationRef cannot cross-contaminate。

---

## 28. Acceptance gates

### R3 Architecture Gate

在 implementation 前必须确认：

```text
Memory read/write split            ACCEPTED
observation side-effect semantics  ACCEPTED
V5 compatibility strategy         ACCEPTED
B.7/B.8 coexistence               ACCEPTED
package dependency direction      ACCEPTED
context injection gate            ACCEPTED
```

### R3 Final Gate

R3-E 最终才允许：

```text
R3 MemoryPort Integration PASS / ACCEPTED
```

R3-A/B/C 各阶段不能自行宣称整个 R3 accepted。

---

## 29. Non-goals

R3 不做：

- 重写 Memory V5 ranking；
- 更换 Memory database；
- 新的 vector database；
- RAG framework migration；
- OpenClaw → Core WeChat migration；
- Native Voice → Core；
- Vision；
- Live2D；
- robot；
- distributed Core；
- universal background reconciler；
- 把 Memory 变成 Core 的 required singleton；
- 打开 production recall gate 作为“测试 R3”的手段。

---

## 30. Known design risks

### Risk A — `/recall` 隐式 current-turn write

Mitigation：先 durable user observation，再 recall；R3-A 不直连 production recall。

### Risk B — V5 observation schema 缺 PresentationObservation

Mitigation：Core semantic contract 不降级；adapter unsupported 必须显式；必要时 provider-side additive compatibility extension。

### Risk C — B.7/B.8 sampling contamination

Mitigation：real production shadow delayed，或使用明确可排除 audit source；默认 isolated DB。

### Risk D — Memory failure accidentally blocks Turn

Mitigation：optional capability + typed unavailable result + operation failure independent from Core semantic ledger。

### Risk E — provider schema leakage

Mitigation：V5 dict → adapter mapping → normalized Core contract；禁止 raw response through port。

### Risk F — duplicate memory writes after uncertainty

Mitigation：R2-D stable OperationId + V5 idempotency key + controlled same-ID reconciliation。

---

## 31. Review questions before R3-A implementation

本 design v0.1 进入 architecture review 时，重点只需要决定：

1. `MemoryRecallPort` read/write split 是否接受；
2. R3-A 是否严格 fake-only / isolated-only；
3. user-observe-before-recall 是否作为 real V5 integration hard invariant；
4. PresentationObservation compatibility gap 如何处理；
5. Memory V5 adapter 是否固定放在 `integrations/memory_v5`；
6. B.7/B.8 解锁 R3-C 的条件；
7. R3-D 的 production gate 条件；
8. 是否需要 additive `/v1/memory/observations` provider endpoint，还是先保持 unsupported。

这些问题在 implementation 前解决，不在写代码途中改变架构目标。

---

## 32. Proposed phase status

```text
R2 Core Foundation          ACCEPTED / MERGED

R3 MemoryPort Integration   DESIGN / PROPOSED
R3-A Memory Read Port       NOT STARTED
R3-B Observation Operations NOT STARTED
R3-C Memory V5 Shadow       NOT STARTED / B.7-B.8 CONSTRAINED
R3-D Recall Context         BLOCKED BY MEMORY PRODUCTION GATE
R3-E Final Gate             NOT STARTED
```

---

## 33. Definition of success

R3 成功不是“Core 能请求到一条 memory”。

真正成功标准是：

```text
Memory V5 exists
→ Core can use it through typed boundaries

Memory V5 crashes
→ Core still works

Memory V5 is removed
→ Core still imports / starts

Memory write becomes uncertain
→ same OperationId reconciles safely

Memory recall is disabled
→ model behavior has no hidden provider injection

Memory policy changes
→ Core does not need provider-specific rewrite
```

达到这一点后，Memory 才真正从“OpenClaw/social-layer 内部功能”升级为 Yogurt 的独立 capability。
