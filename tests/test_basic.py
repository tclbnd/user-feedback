import unittest

from bnkkit.bnk import parse

from banks import bank, bkhd, media


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(bkhd(140, 77))
        self.assertEqual((out["version"], out["bank_id"], out["chunks"]), (140, 77, ["BKHD"]))

    def test_media_list(self):
        d1, d2 = media([(5, 100), (6, 200)])
        out = parse(bkhd() + d1 + d2)
        self.assertEqual([m["id"] for m in out["media"]], [5, 6])
