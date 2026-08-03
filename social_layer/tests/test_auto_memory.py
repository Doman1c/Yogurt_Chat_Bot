from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yogurt_social.auto_memory import AutoMemoryStore, DeterministicMemoryExtractor


class AutoMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "memory.sqlite3"
        self.store = AutoMemoryStore(self.db_path, promotion_threshold=0.78)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_explicit_name_is_promoted(self) -> None:
        results = self.store.process_chat(
            user_id="owner",
            user_message="我叫高毅，以后可以这样称呼我。",
            assistant_reply="记住啦。",
        )
        self.assertEqual(results[0].kind, "fact")
        self.assertEqual(results[0].key, "name")
        self.assertEqual(results[0].status, "promoted")
        snapshot = self.store.base.snapshot("owner")
        self.assertIn(
            {"fact_key": "name", "fact_value": "高毅", "confidence": 0.94},
            snapshot["facts"],
        )

    def test_low_importance_preference_starts_as_candidate_then_promotes_on_repeat(self) -> None:
        first = self.store.process_chat(
            user_id="owner",
            user_message="我喜欢回复自然一点。",
        )
        self.assertEqual(first[0].status, "pending")
        second = self.store.process_chat(
            user_id="owner",
            user_message="我喜欢回复自然一点。",
        )
        self.assertEqual(second[0].status, "promoted")
        self.assertEqual(second[0].occurrences, 2)

    def test_explicit_update_replaces_old_fact_and_records_revision(self) -> None:
        self.store.base.add_fact("owner", "location", "重庆", source="manual")
        results = self.store.process_chat(
            user_id="owner",
            user_message="我的居住地改成了东京。",
        )
        self.assertEqual(results[0].status, "promoted")
        self.assertEqual(results[0].action, "updated")
        self.assertEqual(results[0].conflict_kind, "replacement")
        snapshot = self.store.base.snapshot("owner")
        location = next(item for item in snapshot["facts"] if item["fact_key"] == "location")
        self.assertEqual(location["fact_value"], "东京")

    def test_same_fact_reaffirms_instead_of_duplicating(self) -> None:
        self.store.base.add_fact("owner", "model", "qwen3.5:9b", confidence=0.7)
        results = self.store.process_chat(
            user_id="owner",
            user_message="我的模型是 qwen3.5:9b。",
        )
        self.assertEqual(results[0].status, "merged")
        self.assertEqual(results[0].action, "reaffirmed")
        snapshot = self.store.base.snapshot("owner")
        self.assertEqual(len([item for item in snapshot["facts"] if item["fact_key"] == "model"]), 1)

    def test_sensitive_message_is_never_extracted(self) -> None:
        extractor = DeterministicMemoryExtractor()
        self.assertEqual(extractor.extract("我的 API key 是 abcdef123456。"), [])
        self.assertEqual(extractor.extract("验证码是 123456。"), [])

    def test_assistant_behavior_preference_is_high_importance(self) -> None:
        results = self.store.process_chat(
            user_id="owner",
            user_message="我希望你平时不要反复问我要不要继续。",
        )
        self.assertEqual(results[0].kind, "preference")
        self.assertEqual(results[0].key, "assistant_behavior")
        self.assertEqual(results[0].status, "promoted")

    def test_personal_milestone_remains_candidate_when_temporary(self) -> None:
        results = self.store.process_chat(
            user_id="owner",
            user_message="我今天终于完成了这个测试。",
        )
        self.assertEqual(results[0].kind, "episode")
        self.assertEqual(results[0].status, "pending")


if __name__ == "__main__":
    unittest.main()
