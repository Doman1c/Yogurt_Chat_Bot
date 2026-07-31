import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from yogurt_social.openclaw_adapter import OpenClawAdapter
from yogurt_social.service import SocialLayerService


class _StubService:
    def analyze(self, user_id, message):
        return {
            "state": {"scene": "technical", "mood": "curious"},
            "memories": [{"kind": "episode", "text": "example"}],
            "context_block": "[Yogurt Social State]\n场景：technical",
        }


class _FailingService:
    def analyze(self, user_id, message):
        raise RuntimeError("simulated failure")


class OpenClawAdapterTests(unittest.TestCase):
    def test_preserves_original_message_and_adds_separate_context(self):
        message = "  OpenClaw 配置报错了，怎么修复？\n"
        result = OpenClawAdapter(_StubService()).prepare("owner", message)

        self.assertEqual(result.original_message, message)
        self.assertEqual(result.final_model_input["user_message"], message)
        self.assertEqual(
            result.final_model_input["prepend_system_context"],
            result.social_context,
        )
        self.assertTrue(result.metadata["applied"])
        self.assertTrue(result.metadata["original_message_preserved"])

    def test_fail_open_keeps_original_message(self):
        message = "这条消息必须继续交给原来的 OpenClaw"
        result = OpenClawAdapter(_FailingService()).prepare("owner", message)

        self.assertEqual(result.original_message, message)
        self.assertEqual(result.final_model_input["user_message"], message)
        self.assertEqual(result.social_context, "")
        self.assertTrue(result.metadata["fail_open"])
        self.assertFalse(result.metadata["applied"])

    def test_disabled_adapter_does_not_call_social_layer(self):
        message = "保持原流程"
        result = OpenClawAdapter(_FailingService(), enabled=False).prepare(
            "owner", message
        )

        self.assertEqual(result.original_message, message)
        self.assertEqual(result.social_context, "")
        self.assertEqual(result.metadata["reason"], "disabled")

    def test_real_service_reads_explicit_memory_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SocialLayerService(Path(tmp) / "memory.sqlite3")
            service.memory.upsert_user("owner", "Domanic", "owner")
            service.memory.add_episode(
                "owner",
                "我们一起建立了 Yogurt Chat Bot 的 GitHub 私有仓库",
                importance=0.9,
            )
            result = OpenClawAdapter(service).prepare(
                "owner", "GitHub 仓库的 OpenClaw 适配器怎么做？"
            )

        self.assertIn("[Yogurt Social State]", result.social_context)
        self.assertIn("GitHub 私有仓库", result.social_context)
        self.assertEqual(result.metadata["memory_count"], 1)

    def test_analyze_does_not_write_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SocialLayerService(Path(tmp) / "memory.sqlite3")
            result = OpenClawAdapter(service).prepare("new-user", "酸奶在吗")
            snapshot = service.memory.snapshot("new-user")

        self.assertTrue(result.metadata["applied"])
        self.assertIsNone(snapshot["user"])
        self.assertEqual(snapshot["facts"], [])
        self.assertEqual(snapshot["preferences"], [])
        self.assertEqual(snapshot["episodes"], [])


class OpenClawBridgeTests(unittest.TestCase):
    def test_module_bridge_returns_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.sqlite3"
            request = json.dumps(
                {"user_id": "owner", "message": "OpenClaw 配置报错了"},
                ensure_ascii=False,
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "yogurt_social.openclaw_adapter",
                    "--db",
                    str(db_path),
                ],
                input=request,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["original_message"], "OpenClaw 配置报错了")
        self.assertTrue(payload["metadata"]["applied"])
        self.assertIn("prepend_system_context", payload["final_model_input"])


if __name__ == "__main__":
    unittest.main()
