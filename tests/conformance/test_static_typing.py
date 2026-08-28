import subprocess
import sys
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def test_negative_identifier_fixture_fails_mypy():
    fixture = Path(__file__).parents[1] / "typing" / "negative_ids.py"
    with TemporaryDirectory() as cache_dir:
        environment = dict(os.environ)
        environment["MYPY_CACHE_DIR"] = cache_dir
        result = subprocess.run([sys.executable, "-m", "mypy", str(fixture)], capture_output=True, text=True, check=False, env=environment)
    assert result.returncode != 0
    assert "incompatible type" in result.stdout
    for type_name in ("TraceId", "ResponseId", "OperationAttemptId", "RuntimeInstanceId", "BindingId", "HypothesisId"):
        assert type_name in result.stdout
