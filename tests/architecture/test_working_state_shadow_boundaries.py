"""W2 is an opt-in analysis leaf, never a production dependency."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (name.name for name in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def test_no_core_native_model_legacy_or_production_shadow_dependency():
    for package in ("yogurt_core", "yogurt_native", "social_layer/src", "integrations/openclaw", "integrations/memory_v5"):
        for path in (ROOT / package).rglob("*"):
            if path.suffix in {".py", ".ts"}:
                assert "working_state_shadow" not in path.read_text(encoding="utf-8"), path
    for path in (ROOT / "yogurt_core").rglob("*.py"):
        assert not any(name.startswith(("yogurt_social", "social_layer", "integrations")) for name in imports(path)), path
    for path in (ROOT / "social_layer/src").rglob("*.py"):
        assert not any(name.startswith("yogurt_core") for name in imports(path)), path


def test_comparator_and_contracts_have_no_runtime_dependency():
    for file in ("comparison.py", "contracts.py"):
        path = ROOT / "integrations/working_state_shadow" / file
        assert not any(name.startswith(("yogurt_core", "yogurt_social", "social_layer", "socket", "urllib")) for name in imports(path))
    replay = (ROOT / "integrations/working_state_shadow/replay.py").read_text(encoding="utf-8")
    assert "WorkingStateProjectionService" in replay
    assert "TemporaryDirectory" in replay
    assert "YogurtCore(" not in replay and "datetime.now" not in replay
