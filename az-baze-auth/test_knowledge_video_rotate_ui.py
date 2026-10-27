from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "templates" / "knowledge_view.html"


class KnowledgeVideoRotateUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TEMPLATE.read_text(encoding="utf-8")

    def test_rotation_control_is_video_only_and_single(self):
        self.assertEqual(self.text.count('id="videoRotateButton"'), 1)
        self.assertIn('aria-label="Повернуть видео на 90 градусов"', self.text)

    def test_rotation_cycles_by_quarter_turns(self):
        self.assertIn("rotation = (rotation + 90) % 360", self.text)
        self.assertIn("video.style.transform = 'rotate(' + rotation + 'deg)'", self.text)

    def test_rotation_refits_without_reloading_media(self):
        self.assertIn("const fitVideo = () =>", self.text)
        self.assertIn("window.addEventListener('resize', fitVideo)", self.text)
        self.assertNotIn("video.currentTime =", self.text)
        self.assertNotIn("video.load()", self.text)

    def test_existing_speed_control_remains(self):
        self.assertIn("data-playback-rate", self.text)
        self.assertIn("video.playbackRate = rate", self.text)


if __name__ == "__main__":
    unittest.main()
