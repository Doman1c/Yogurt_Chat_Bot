# R3-W4 — Legacy ConversationState authority downgrade

W4 is merged. W5 closes the string-literal guard gap and adds final conformance;
see [current track status](runtime-r3w-status.md). The scope below describes W4.

Core ConversationWorkingState is the sole authority for current conversational
cognition in the new Core runtime. W4 establishes ownership, not a production
migration. The existing WeChat/OpenClaw path remains a legacy compatibility path.

| Data | Owner after W4 | Permitted use |
| --- | --- | --- |
| active_threads, recent_focus, unresolved_items | Core WorkingState | Derived from authoritative Core facts; admitted by W3 Core context policy |
| current_topic, open_threads | Legacy/provider-internal, deprecated structural compatibility | Existing legacy storage, reducer, planner and prompt behavior; offline W2 comparison |
| dialogue_act, user_mood, interaction_mode, recent_actions, last_response_pattern | Legacy-only compatibility | Existing legacy behavior remains operational |

“Deprecated” applies to structural **authority and new runtime use**, not to
immediate deletion of fields or removal from existing prompts. No schema, stored
data, TTL, classifier, planner result or injection string changes in W4. No
runtime warning is emitted: that could change CLI/protocol output. Source-level
ownership notices and permanent architecture tests make the boundary explicit.

## Boundaries

Core must not import Legacy ConversationState, its store/runtime/planner, or W2.
Legacy state cannot be a WorkingState source, a fallback, an override, a merge
input, or a ModelContext input. New Core adapters and Native code cannot read
current_topic/open_threads or the legacy database. Core facts → W1 → W3 remains
the only current-cognition context path; persona and history keep their separate
ownership. W2 remains offline evidence and does not promote Legacy into authority.

Current legacy response planning still uses topic/thread continuity. Removing
those fields from production injection would risk visible behavior changes, so
that behavior is explicitly retained as legacy-only compatibility. Existing
legacy Memory benchmark/gate consumers are not modified or reinterpreted in W4.
This is not approval for new consumers or for Memory → Core context injection.

## Evidence

Architecture tests forbid legacy imports and structural field references in
Core, Core adapters and Native, including dynamic import string literals.
Behavioral tests mutate real isolated Legacy databases (including hostile
topic/thread values) while comparing actual W1 projections and W3 composition
for both SQLite and InMemory Core. Separate complete Core conversations produce
identical captured model contexts despite divergent Legacy databases. Legacy
compatibility mood/mode/action/pattern updates are checked using its real engine.

W2/W3 regression gates remain unchanged. Production Python executable ASTs and
schema literals in the three annotated Legacy modules are pinned to the reviewed
W3 baseline, ignoring documentation only. This protects visible logic while
allowing the ownership notices. Existing OpenClaw, V4 state/runtime and full
regressions run in the unchanged Python 3.9–3.12 CI matrix.

No production database or service is needed by these tests. All mutable Legacy
and Core databases are test-owned temporary files. No production Memory contact,
B.7/B.8 change, recall enablement, WeChat migration, W5 cleanup, R3-D/R3-E,
Voice/Singing change or S2.5/iOS work is included. Removing the legacy production
continuity path requires a separately approved migration; W4 does not perform it.
