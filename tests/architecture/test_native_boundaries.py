"""Prove Native works in a process where optional Surface packages cannot import."""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_core_has_no_surface_or_http_imports():
    forbidden = {"fastapi", "starlette", "uvicorn", "httpx", "urllib", "yogurt_native", "openclaw", "social_layer", "voice", "voice_runtime"}
    for package in ("yogurt_core", "yogurt_core_domain", "yogurt_contracts"):
        for file in (ROOT / package).rglob("*.py"):
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                assert not any(module.split(".")[0] in forbidden for module in modules), file

def test_native_runs_with_social_packages_absent(tmp_path):
    script = r'''
import sys, importlib.abc, asyncio
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"openclaw", "wechat", "social_layer", "tencent_weixin"}:
            raise ImportError("package intentionally absent")
sys.meta_path.insert(0, Absent())
from tests.native.test_gateway import setup, post, close
from pathlib import Path
async def run():
    core, store, gateway, profile, client, model, app = await setup(Path(sys.argv[1]))
    result = await post(client)
    await core.drain()
    assert "response.completed" in (await client.get(result["event_stream_url"])).text
    await close(core, store, client)
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path / "core.db")], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
