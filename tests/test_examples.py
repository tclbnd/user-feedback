"""The two sample files shipped with the repository."""
import os
import unittest

from webpkit.webp import parse

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "examples")


def load(name):
    with open(os.path.join(EXAMPLES, name), "rb") as fh:
        return parse(fh.read())


class StickerTests(unittest.TestCase):
    def test_canvas(self):
        out = load("sticker.webp")
        self.assertEqual(out["canvas"], {"width": 320, "height": 320})
        self.assertTrue(out["animated"])
        self.assertTrue(out["alpha"])

    def test_frames(self):
        out = load("sticker.webp")
        self.assertEqual(len(out["frames"]), 7)
        for f in out["frames"][:6]:
            self.assertEqual((f["x"], f["y"], f["width"], f["height"]), (80, 80, 160, 160))
            self.assertEqual((f["duration"], f["blend"], f["dispose"]), (60, False, True))
            self.assertEqual((f["codec"], f["alpha"]), ("lossy", True))
        last = out["frames"][6]
        self.assertEqual((last["x"], last["y"], last["width"], last["height"]), (0, 0, 320, 320))
        self.assertEqual((last["duration"], last["blend"], last["dispose"]), (400, True, False))
        self.assertEqual((last["codec"], last["alpha"]), ("lossless", True))

    def test_duration(self):
        self.assertEqual(load("sticker.webp")["duration"], 760)


class BannerTests(unittest.TestCase):
    def test_canvas_and_background(self):
        out = load("banner.webp")
        self.assertEqual(out["canvas"], {"width": 728, "height": 90})
        self.assertEqual(out["background"], "#996633")
        self.assertEqual(out["background_alpha"], 0xFF)
        self.assertEqual(out["loop_count"], 2)
        self.assertTrue(out["exif"])

    def test_frames(self):
        out = load("banner.webp")
        self.assertEqual(len(out["frames"]), 2)
        first, second = out["frames"]
        self.assertEqual((first["x"], first["y"], first["width"], first["height"]), (0, 0, 728, 90))
        self.assertEqual((first["duration"], first["blend"], first["dispose"]), (3000, True, False))
        self.assertEqual((second["x"], second["y"], second["width"], second["height"]), (214, 24, 300, 40))
        self.assertEqual((second["duration"], second["blend"], second["dispose"]), (1500, True, True))

    def test_duration(self):
        self.assertEqual(load("banner.webp")["duration"], 4500)

    def test_no_still_image_for_animation(self):
        self.assertIsNone(load("banner.webp")["image"])


if __name__ == "__main__":
    unittest.main()
