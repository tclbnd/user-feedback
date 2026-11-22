import unittest

from fsbkit.fsb import parse

from banks import fsb


class BasicTests(unittest.TestCase):
    def test_bank(self):
        out = parse(fsb([(8, 1, 1000, [])], [4096], version=0, codec=2, names=["click"]))
        self.assertEqual((out["codec"], out["sample_count"], out["samples"][0]["name"]), ("pcm16", 1, "click"))

    def test_mono(self):
        s = parse(fsb([(8, 0, 500, [])], [1024], version=0, codec=1))["samples"][0]
        self.assertEqual((s["channels"], s["samples"]), (1, 500))
