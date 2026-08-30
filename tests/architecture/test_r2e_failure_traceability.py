"""Keep the R2-E FS-01..FS-32 evidence index complete and executable."""

import ast
import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
MATRIX = ROOT / "docs" / "runtime" / "R2_FINAL_CONFORMANCE_MATRIX.md"
ROW = re.compile(r"^\| FS-(\d{2}) \|.*\| (PASS|PARTIAL|MISSING) \|.*$")
REFERENCE = re.compile(r"`([^`]+\.py)::([A-Za-z_][A-Za-z0-9_]*)`")


def test_failure_simulation_matrix_has_all_frozen_ids_and_real_test_references() -> None:
    rows = []
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match is not None:
            rows.append((int(match.group(1)), match.group(2), line))
    assert [item[0] for item in rows] == list(range(1, 33))
    assert {item[1] for item in rows} == {"PASS"}
    for _, _, line in rows:
        references = REFERENCE.findall(line)
        assert references, line
        for relative_path, test_name in references:
            path = ROOT / relative_path
            assert path.is_file(), relative_path
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            assert any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name for node in ast.walk(tree)), test_name
