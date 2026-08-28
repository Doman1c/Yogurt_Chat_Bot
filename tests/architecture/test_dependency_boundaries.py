"""Allowlist architecture checks for R1 production contract packages."""

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]
ALLOWED_CONTRACT_ROOTS = {"__future__", "typing", "datetime", "enum", "hashlib", "json", "unicodedata", "collections", "abc", "pydantic", "yogurt_contracts"}
ALLOWED_CORE_ROOTS = ALLOWED_CONTRACT_ROOTS | {"yogurt_core_domain"}


def _imports(package: str):
    for path in (ROOT / package).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    yield path, name.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                yield path, node.module.split(".")[0]


def test_public_contract_imports_use_allowlist():
    violations = [(path, name) for path, name in _imports("yogurt_contracts") if name not in ALLOWED_CONTRACT_ROOTS]
    assert not violations, violations


def test_core_domain_imports_use_allowlist():
    violations = [(path, name) for path, name in _imports("yogurt_core_domain") if name not in ALLOWED_CORE_ROOTS]
    assert not violations, violations


def test_public_contract_does_not_depend_on_internal_core_domain():
    imports = [name for _, name in _imports("yogurt_contracts")]
    assert "yogurt_core_domain" not in imports


def test_public_contract_and_conformance_tests_do_not_import_internal_ledger():
    forbidden = []
    for test_root in (ROOT / "tests" / "contracts", ROOT / "tests" / "conformance"):
        for path in test_root.rglob("*.py"):
            if "yogurt_core_domain" in path.read_text(encoding="utf-8"):
                forbidden.append(path)
    assert not forbidden, forbidden
