"""W1 must run with optional Memory, legacy and Surface packages absent."""

import ast
from pathlib import Path
import subprocess
import sys


def test_working_state_imports_and_runs_without_memory_or_legacy():
    script = '''
import sys
from importlib.abc import MetaPathFinder
class BlockOptional(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("integrations", "social_layer", "yogurt_social", "yogurt_native", "yogurt_contracts.memory", "yogurt_core.memory")):
            raise ImportError("optional dependency deliberately absent: " + fullname)
if len(sys.argv) > 1:
    sys.meta_path.insert(0, BlockOptional())
from yogurt_core.context.working_state import WorkingStateDeriver, WorkingStateTurnSource
from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.foundation.identifiers import ConversationId, PersonaId, UserId, TurnId
ref = ConversationRef(persona_id=PersonaId(value="p"), user_id=UserId(value="u"), conversation_id=ConversationId(value="c"))
turn = TurnRef(conversation_id=ref.conversation_id, turn_id=TurnId(value="t"))
result = WorkingStateDeriver().derive(ref, turn, (WorkingStateTurnSource(turn, "hello?", False),))
assert result.unresolved_items[0].value == "hello?"
print(result.model_dump_json())
'''
    completed = subprocess.run([sys.executable, "-c", script, "absent"], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert 'working-state-v1' in completed.stdout
    installed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert installed.returncode == 0, installed.stderr
    assert installed.stdout == completed.stdout


def test_projection_has_no_model_or_legacy_wiring():
    root = Path(__file__).resolve().parents[2]
    for filename in ("yogurt_core/context/working_state.py", "yogurt_contracts/conversation/working_state.py"):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        assert not any(any(part in module for part in ("memory", "social", "native", "model.execution", "time", "random", "uuid")) for module in imports)
    # W3 explicitly admits derived data through ModelContext. W1's deriver
    # remains provider/model independent; W3's authority seam is tested in
    # test_working_state_context_boundaries.py.
