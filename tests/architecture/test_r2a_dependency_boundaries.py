"""R2-A core stays detached from production integrations and heavy runtimes."""

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]
FORBIDDEN = {"openclaw", "integrations", "social_layer", "voice", "voice_runtime", "voice_orchestrator", "torch", "ollama", "yogurt_conformance"}


def _imports(package: str):
    for path in (ROOT / package).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    yield path, name.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                yield path, node.module.split(".")[0]


def test_r2a_core_does_not_import_production_or_conformance_packages():
    violations = [(path, root) for package in ("yogurt_core", "yogurt_core_adapters") for path, root in _imports(package) if root in FORBIDDEN]
    assert not violations, violations
