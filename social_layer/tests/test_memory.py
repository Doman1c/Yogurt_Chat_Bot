import tempfile
import unittest
from pathlib import Path

from yogurt_social.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.tmp.name) / "memory.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_fact_upsert(self) -> None:
        self.store.add_fact("owner", "常住地", "重庆")
        self.store.add_fact("owner", "常住地", "重庆两江新区")
        snapshot = self.store.snapshot("owner")
        self.assertEqual(len(snapshot["facts"]), 1)
        self.assertEqual(snapshot["facts"][0]["fact_value"], "重庆两江新区")

    def test_relevant_preference(self) -> None:
        self.store.add_preference("owner", "回答风格", "客服式回答", polarity=-1)
        results = self.store.relevant("owner", "不要用客服式语气回答", limit=3)
        self.assertTrue(results)
        self.assertEqual(results[0].kind, "preference")

    def test_episode_retrieval(self) -> None:
        self.store.add_episode("owner", "我们一起完成了 Yogurt Chat Bot 的 GitHub 私有仓库")
        results = self.store.relevant("owner", "GitHub 仓库下一步做什么", limit=3)
        self.assertTrue(any(item.kind == "episode" for item in results))


if __name__ == "__main__":
    unittest.main()
