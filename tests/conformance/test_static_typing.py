import subprocess
import sys
from pathlib import Path


def test_negative_identifier_fixture_fails_mypy():
    fixture = Path(__file__).parents[1] / "typing" / "negative_ids.py"
    result = subprocess.run([sys.executable, "-m", "mypy", str(fixture)], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "incompatible type" in result.stdout
