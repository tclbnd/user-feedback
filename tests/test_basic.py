import unittest

from sndkit.snd import parse

from files import au


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(au(1, bytes([0xFF]) * 800))
        self.assertEqual((out["encoding"], out["sample_rate"], out["frames"], out["duration"]), ("mulaw", 8000, 800, "1/10"))

    def test_mulaw_silence(self):
        self.assertEqual(parse(au(1, bytes([0xFF]) * 80))["peak"], "0.000000")
