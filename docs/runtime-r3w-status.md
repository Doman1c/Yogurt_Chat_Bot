# R3-W final ownership and conformance status

This is the current status; W1–W4 design documents retain their historical scope.

| Stage | Status |
| --- | --- |
| W1 deterministic Core WorkingState | ✅ merged |
| W2 isolated Legacy/Core shadow comparison | ✅ merged |
| W3 user-authority WorkingState context | ✅ merged |
| W4 Legacy authority downgrade | ✅ merged |
| W5 final ownership conformance | Current/final implementation; awaiting review |
| R3-D Memory evidence → ModelContext | **BLOCKED by B.7/B.8**; not implemented |

Core WorkingState alone owns `active_threads`, `recent_focus`, `unresolved_items`
for current cognition in Core-backed runtime. Legacy `current_topic` and
`open_threads` are deprecated compatibility-only. Legacy mood, dialogue act,
interaction mode, recent actions and response pattern remain legacy-only.
Existing WeChat/OpenClaw behavior is retained; closing R3-W does not migrate it.

W5 changes tests and documentation only. The W4 architecture guard now checks
all executable string/bytes literals, identifiers and import aliases, exempting
only genuine docstrings. Mutation tests cover dictionary keys, attributes via
getattr, variable-held dynamic import names and direct imports. This static guard
is not a general deobfuscator for arbitrary computed strings or hostile code.

The final Native conformance test compares W1 state, ContextProjection,
PersonaSnapshot, composed ModelContext, captured model inputs, HTTP SSE replay
and Native history before/after isolated Legacy DB mutation and SQLite rebuild.
A second test directly compares SQLite and InMemory state/context at the same
through-turn boundary. W3's authority-isolation and identical-context retry tests
remain permanent gates; no new persona/system authority is granted to user data.

W1 derivation version, equality, expiry and bounds; W2 comparator; W3 composer;
and W4 compatibility semantics remain unchanged. No schema deletion, NLP tuning,
Memory production access, recall enablement, B.7/B.8 changes, R3-E, Voice/Singing,
S2.5 or iOS work is included. All new tests are already covered by the existing
Python 3.9–3.12 CI suites. No PR or merge is authorized by this implementation.

R3-D remains blocked pending the existing B.7/B.8 evidence and explicit approval:
the production gate remains 0.52, real recall is disabled, and no Memory evidence
is injected into prompts. The promotion requirement remains at least 500
gate-evaluated events plus benchmark safety and manual/labeled review. W5 neither
evaluates nor changes those counters, thresholds or promotion decisions.
