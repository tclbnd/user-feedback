import unittest

from nsvkit.nsv import parse

from frames import sync_frame


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(sync_frame())
        self.assertEqual((out["video_format"], out["width"], out["height"], out["header"]), ("VP62", 320, 240, None))

    def test_payload(self):
        out = parse(sync_frame(100, 20) * 2)
        self.assertEqual((out["frames"], out["video_bytes"], out["audio_bytes"]), (2, 200, 40))
