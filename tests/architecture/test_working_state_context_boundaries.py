"""W3 is Core cognition, not Memory recall, legacy injection or client state."""

import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_context_import_boundaries_and_native_policy_absence():
    for path in (ROOT / "yogurt_core").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(any(x in name for x in ("social_layer", "yogurt_social", "working_state_shadow")) for name in imports)
        if path.parent.name == "context":
            assert not any("memory" in name for name in imports)
    for path in (ROOT / "yogurt_native").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name != "model.py":
            assert "working_state" not in text.lower()
        else:
            assert "context.working_state.material" in text
            assert not any(term in text for term in ("active_threads", "unresolved_items", "recent_focus", "1200", "WorkingStateProjection"))
    core = (ROOT / "yogurt_core/application/core.py").read_text(encoding="utf-8")
    assert core.count("await self._composer.compose(turn)") == 1
    assert core.index("await self._composer.compose(turn)") < core.index("for _ in (1, 2)")


def test_core_runs_with_memory_legacy_shadow_absent_and_no_network():
    script = '''
import sys, asyncio, socket
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("integrations", "yogurt_core.memory", "yogurt_contracts.memory", "social_layer", "yogurt_social", "yogurt_native")):
            raise ImportError("forbidden optional dependency: " + fullname)
sys.meta_path.insert(0, Block())
from tests.core.test_ephemeral_core import _core, _command
from yogurt_core_adapters.model_fake import FakeModelAdapter
from yogurt_core.model.events import VisibleTextDelta
async def run():
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    socket.socket.connect = forbidden
    model = FakeModelAdapter(((VisibleTextDelta(text="ok"),), ()))
    core, store = await _core(model=model)
    try:
        await core.submit_turn(_command(text="prior focus"))
        await core.drain()
        await core.submit_turn(_command("t2", "continue"))
        await core.drain()
        assert "prior focus" in model.contexts[-1].working_state.material
    finally:
        await core.stop()
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
