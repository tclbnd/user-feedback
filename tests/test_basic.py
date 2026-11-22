import unittest

from xakit.xa import parse

from rips import cdxa, vcd


class BasicTests(unittest.TestCase):
    def test_sector_count(self):
        out = parse(cdxa(vcd(0, 8)))
        self.assertEqual((out["sectors"], out["modes"], out["form2"]), (8, {"2": 8}, 8))

    def test_kinds(self):
        out = parse(cdxa(vcd(0, 8, audio_every=4)))
        self.assertEqual((out["video"], out["audio"]), (6, 2))
