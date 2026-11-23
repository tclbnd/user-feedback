import unittest

from scckit.scc import parse

from captions import caption, control, scc


class BasicTests(unittest.TestCase):
    def test_header(self):
        with self.assertRaises(ValueError):
            parse("WEBVTT\n")

    def test_caption_text(self):
        out = parse(scc([("00:00:01:00", caption("HI"))]))
        self.assertEqual(out["CC1"][0]["text"], "HI")
