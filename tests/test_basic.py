import unittest

from ltckit.ltc import decode

from takes import ltc_wav


class BasicTests(unittest.TestCase):
    def test_mono(self):
        out = decode(ltc_wav((1, 0, 0, 0), 10, 25, lead=0.1))
        self.assertEqual((out["fps"], out["frames"], out["last"]), ("25", 10, "01:00:00:09"))

    def test_rate(self):
        out = decode(ltc_wav((0, 10, 0, 0), 6, 30, 44100, lead=0.1))
        self.assertEqual((out["sample_rate"], out["fps"], out["first"]), (44100, "30", "00:10:00:00"))
