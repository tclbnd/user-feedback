import unittest

from gifkit.gif import parse

from images import tiny


class BasicTests(unittest.TestCase):
    def test_screen(self):
        out = parse(tiny(32, 20))
        self.assertEqual((out["version"], out["width"], out["height"], out["global_colors"]), ("89a", 32, 20, None))

    def test_frames(self):
        self.assertEqual(len(parse(tiny(frames=3))["frames"]), 3)
