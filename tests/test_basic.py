import unittest

from icykit.icy import scan

from streams import audio, head


class BasicTests(unittest.TestCase):
    def test_headers(self):
        out = scan(head(name="Night Jazz", br="96") + audio(500))
        self.assertEqual((out["protocol"], out["status"], out["name"], out["bitrate"]), ("ICY", 200, "Night Jazz", 96))

    def test_no_metadata(self):
        out = scan(head() + audio(1200))
        self.assertEqual((out["metaint"], out["audio_bytes"], out["titles"]), (None, 1200, []))
