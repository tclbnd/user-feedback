import unittest

from dvkit.dv import scan

from tapes import clip


class BasicTests(unittest.TestCase):
    def test_ntsc(self):
        out = scan(clip(4, False, start=(0, 0, 0, 0)))
        self.assertEqual((out["system"], out["frames"], out["frame_rate"]), ("525-60", 4, "30000/1001"))

    def test_audio(self):
        a = scan(clip(2, False, audio={"rate": 48000}))["audio"]
        self.assertEqual((a["sample_rate"], a["channels"], a["locked"]), (48000, 2, True))
