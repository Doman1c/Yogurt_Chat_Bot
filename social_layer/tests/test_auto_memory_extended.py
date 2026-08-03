from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yogurt_social.auto_memory import AutoMemoryStore
from yogurt_social.auto_memory_extended import ExtendedDeterministicMemoryExtractor


class ExtendedAutoMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "memory.sqlite3"
        self.store = AutoMemoryStore(self.db_path, promotion_threshold=0.78)
        self.extractor = ExtendedDeterministicMemoryExtractor()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def process(self, message: str):
        return self.store.process_chat(
            user_id="owner",
            user_message=message,
            assistant_reply="收到。",
            extractor=self.extractor,
        )

    def test_sticker_over_emoji_preference_promotes(self) -> None:
        results = self.process("尽量多用表情包而不是emoji吧")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].kind, "preference")
        self.assertEqual(results[0].key, "assistant_behavior")
        self.assertEqual(results[0].value, "优先使用表情包而不是emoji")
        self.assertEqual(results[0].status, "promoted")

    def test_multi_bubble_clause_is_extracted_without_repeating_base_preference(self) -> None:
        results = self.process(
            "酸奶，我希望你平时说话自然一点，适合的时候把回复分成两到三个短气泡。"
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].value, "适合时将回复分成两到三个短气泡")
        self.assertEqual(results[0].status, "promoted")

    def test_academic_profile_and_temporary_location_are_separated(self) -> None:
        results = self.process(
            "目前我在纽约哥大攻读生物医学工程的ms学位，现在是暑假，回重庆了"
        )
        by_key = {item.key: item for item in results}
        self.assertEqual(by_key["school"].value, "纽约哥大")
        self.assertEqual(by_key["major"].value, "生物医学工程")
        self.assertEqual(by_key["degree"].value, "MS")
        self.assertEqual(by_key["school"].status, "promoted")
        self.assertEqual(by_key["major"].status, "promoted")
        self.assertEqual(by_key["degree"].status, "promoted")
        self.assertEqual(by_key["current_location"].value, "重庆")
        self.assertEqual(by_key["current_location"].status, "pending")

        snapshot = self.store.base.snapshot("owner")
        facts = {item["fact_key"]: item["fact_value"] for item in snapshot["facts"]}
        self.assertEqual(facts["school"], "纽约哥大")
        self.assertEqual(facts["major"], "生物医学工程")
        self.assertEqual(facts["degree"], "MS")
        self.assertNotIn("current_location", facts)

    def test_base_only_phrase_is_not_duplicated_by_extension(self) -> None:
        self.assertEqual(self.extractor.extract("我希望你平时说话自然一点。"), [])


if __name__ == "__main__":
    unittest.main()
