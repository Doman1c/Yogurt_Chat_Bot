"""W4 ownership and compatibility, independent of optional legacy imports."""

import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_core_and_native_have_no_legacy_authority_inputs():
    forbidden = ("social_layer", "yogurt_social", "conversation_state_runtime",
                 "working_state_shadow", "current_topic", "open_threads", "ConversationState")
    for package in ("yogurt_core", "yogurt_core_adapters", "yogurt_core_domain", "yogurt_native"):
        for path in (ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # Include dynamic-import literals and field keys, not only imports.
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # Ignore documentation (checked separately by import rules).
                    continue
                if isinstance(node, ast.ImportFrom):
                    assert not any(term in (node.module or "") for term in forbidden), path
                if isinstance(node, ast.Import):
                    assert not any(term in alias.name for alias in node.names for term in forbidden), path
                if isinstance(node, (ast.Name, ast.Attribute)):
                    value = node.id if isinstance(node, ast.Name) else node.attr
                    assert not any(term in value for term in forbidden), path
            for node in ast.walk(tree):
                if isinstance(node, (ast.Call, ast.Subscript)):
                    assert not any(term in ast.dump(node) for term in forbidden), path


def executable_fingerprint(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:]
        # Python 3.12 adds empty type_params; normalize this version-only detail.
        node._fields = tuple(field for field in node._fields if field != "type_params")
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


def test_legacy_executable_logic_and_schema_match_reviewed_baseline():
    # From main 3b77b17, excluding docstrings/comments and AST version metadata.
    expected = {
        "conversation_state.py": "fedb0ec6ef6fdfd6d192003663cba5bde096e7eefe39478d269cdadf9620de75",
        "conversation_state_runtime.py": "4094ce3b1ac0f51d5c1ef18cc38fd1c272a960f50b34c0b35312cc680d63b756",
        "response_planner.py": "3a762cee21b0972fd45f938a1be1675a02e97f7319783e32a93735fd1028bc6a",
    }
    for name, digest in expected.items():
        assert executable_fingerprint(ROOT / "social_layer/src/yogurt_social" / name) == digest
