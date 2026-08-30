"""R3-A removable-provider boundary tests."""

import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]
FORBIDDEN = {
    "social_layer", "integrations", "fastapi", "urllib", "openclaw", "wechat",
    "torch", "gpt_sovits", "voice", "voice_runtime", "voice_orchestrator",
}


def _imports(package: str):
    for path in (ROOT / package).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    yield path, name.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                yield path, node.module.split(".")[0]


def test_r3a_core_memory_code_imports_only_contracts_and_ports() -> None:
    violations = [(path, root) for package in ("yogurt_core", "yogurt_contracts") for path, root in _imports(package) if root in FORBIDDEN]
    assert not violations, violations


def test_core_and_contracts_import_when_memory_and_provider_packages_are_absent() -> None:
    blocked_imports = "social_layer integrations fastapi openclaw wechat torch gpt_sovits"
    program = """
import builtins
blocked = set({blocked!r}.split())
original_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split('.', 1)[0] in blocked:
        raise ImportError('optional provider blocked: ' + name)
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import
import yogurt_contracts
import yogurt_core
import yogurt_contracts.memory.recall
import yogurt_core.memory.recall_policy
import yogurt_core.ports.memory_recall
""".format(blocked=blocked_imports)
    result = subprocess.run([sys.executable, "-c", program], cwd=str(ROOT), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
