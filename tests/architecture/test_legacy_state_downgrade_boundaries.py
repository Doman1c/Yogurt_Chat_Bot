"""W4 ownership and compatibility, independent of optional legacy imports."""

import ast
import hashlib
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("social_layer", "yogurt_social", "conversation_state_runtime",
             "working_state_shadow", "current_topic", "open_threads", "ConversationState")


def assert_no_legacy_inputs(source):
    tree = ast.parse(source)
    # Only genuine module/class/function docstrings are documentation. Arbitrary
    # string expressions, assignments, mapping keys and import names are code.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:]
    for node in ast.walk(tree):
        values = []
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            values.append(node.value.decode("utf-8", errors="replace") if isinstance(node.value, bytes) else node.value)
        if isinstance(node, ast.Name):
            values.append(node.id)
        if isinstance(node, ast.Attribute):
            values.append(node.attr)
        if isinstance(node, ast.ImportFrom):
            values.append(node.module or "")
        if isinstance(node, ast.alias):
            values.extend((node.name, node.asname or ""))
        assert not any(term in value for value in values for term in FORBIDDEN), (getattr(node, "lineno", None), values)


def test_core_and_native_have_no_legacy_authority_inputs():
    for package in ("yogurt_core", "yogurt_core_adapters", "yogurt_core_domain", "yogurt_native"):
        for path in (ROOT / package).rglob("*.py"):
            assert_no_legacy_inputs(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("term", FORBIDDEN)
@pytest.mark.parametrize("template", [
    'name = "{term}"', 'getattr(state, "{term}")',
    'value = {{"{term}": 1}}', 'value = state["{term}"]',
    'module = "pkg.{term}"\nimportlib.import_module(module)',
    '__import__("{term}")', 'value = b"{term}"',
    'from pkg import {term} as permitted',
])
def test_guard_rejects_indirect_legacy_leakage(term, template):
    with pytest.raises(AssertionError):
        assert_no_legacy_inputs(template.format(term=term))


def test_guard_only_exempts_real_docstrings_and_comments():
    assert_no_legacy_inputs('"""current_topic is forbidden"""\n# open_threads\nvalue = "safe"')
    with pytest.raises(AssertionError):
        assert_no_legacy_inputs('value = 1\n"current_topic"')


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
