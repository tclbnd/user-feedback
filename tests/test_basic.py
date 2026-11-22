import unittest

from flickit.flic import parse

from anims import anim, color256, frame, flic, brun


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(anim("FLC", 5, 40, width=640, height=480))
        self.assertEqual((out["width"], out["height"], out["depth"], out["frame_delay_ms"]), (640, 480, 8, "40"))

    def test_palette(self):
        out = parse(flic("FLC", [frame([color256([(10, 20, 30), (40, 50, 60)], start=2), brun()])], 50))
        self.assertEqual(out["first_colors"], [None, None, "#0a141e", "#28323c"])
