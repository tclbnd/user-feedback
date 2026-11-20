import unittest

from aacpack import schedule
from aacpack.adts import parse


class BasicTests(unittest.TestCase):
    def test_header(self):
        f = parse(bytes([0xFF, 0xF1, 0x50, 0x80, 0x2E, 0x7F, 0xFC]))
        self.assertEqual((f.sample_rate, f.channel_config, f.length, f.crc), (44100, 2, 371, False))

    def test_gaps(self):
        files = [("a", 0, 3600), ("b", 3610, 100)]
        self.assertEqual(schedule.gaps(files), [{"after": "a", "before": "b", "seconds": "10"}])
        self.assertEqual(schedule.hour_slots(files), {0: ["a"], 1: ["b"]})


if __name__ == "__main__":
    unittest.main()
