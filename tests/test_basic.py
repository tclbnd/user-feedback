import unittest

from av1kit.av1 import scan

from clips import clip, ivf, seq_header


class BasicTests(unittest.TestCase):
    def test_ivf_header(self):
        out = scan(ivf(clip(5, size=40), width=1920, height=1080))
        self.assertEqual((out["fourcc"], out["width"], out["ivf_frames"], out["temporal_units"]), ("AV01", 1920, 5, 5))

    def test_sequence_header(self):
        s = scan(ivf(clip(2, seq_header(1920, 1080, level=8), size=40)))["sequence"]
        self.assertEqual((s["profile"], s["level"], s["max_width"], s["max_height"]), (0, 8, 1920, 1080))
