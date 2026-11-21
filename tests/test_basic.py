import unittest

from apekit.mac import parse

from headers import new


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(new(version=3990, level=3000, ch=2, rate=48000))
        self.assertEqual((out["version"], out["compression"], out["channels"], out["sample_rate"]), ("3.99", "high", 2, 48000))

    def test_frames(self):
        out = parse(new(frames=7, bpf=73728 * 4))
        self.assertEqual((out["total_frames"], out["blocks_per_frame"], out["seek_entries"]), (7, 294912, 7))
