"""R2-E evidence that Core imports do not require removable providers."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_core_and_contracts_import_with_optional_provider_packages_blocked() -> None:
    blocked_imports = "torch whisper gpt_sovits openclaw wechat live2d robot"
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
""".format(blocked=blocked_imports)
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
