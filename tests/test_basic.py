import unittest

from lufskit.lufs import parse

from signals import stereo_sine


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(stereo_sine(1.0))
        self.assertEqual((out["sample_rate"], out["channels"], out["duration"]), (48000, 2, "1.000"))

    def test_silence(self):
        self.assertIsNone(parse(stereo_sine(1.0, amp=0.0))["integrated"])
