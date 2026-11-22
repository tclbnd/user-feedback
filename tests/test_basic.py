import unittest

from mpckit.mpc import parse

from streams import mpc, se, sh


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(mpc(sh(100, rate=44100, channels=2), se()))
        self.assertEqual((out["version"], out["sample_rate"], out["channels"], out["samples"]), (8, 44100, 2, 100))

    def test_packets(self):
        self.assertEqual(parse(mpc(sh(100), se()))["packets"], ["SH", "SE"])
