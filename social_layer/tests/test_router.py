import unittest

from yogurt_social.models import Mood, ReplyLength, Scene
from yogurt_social.router import StyleRouter


class StyleRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = StyleRouter()

    def test_emotion_has_priority_over_technical_terms(self) -> None:
        state = self.router.analyze("模型又报错了，我真的很难过")
        self.assertEqual(state.scene, Scene.COMFORT)
        self.assertEqual(state.mood, Mood.SAD)

    def test_technical_message(self) -> None:
        state = self.router.analyze("OpenClaw 配置报错了，怎么修复？")
        self.assertEqual(state.scene, Scene.TECHNICAL)
        self.assertEqual(state.reply_length, ReplyLength.MEDIUM)

    def test_celebration_message(self) -> None:
        state = self.router.analyze("连接成功了！")
        self.assertEqual(state.scene, Scene.CELEBRATION)
        self.assertTrue(state.use_sticker)

    def test_default_is_casual(self) -> None:
        state = self.router.analyze("酸奶在吗")
        self.assertEqual(state.scene, Scene.CASUAL)


if __name__ == "__main__":
    unittest.main()
