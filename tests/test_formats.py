import unittest

from xakit.xa import SECTOR, parse


def sector(filenum, channel, submode, code, groups=b""):
    sh = bytes([filenum, channel, submode, code])
    body = groups + bytes(2328 - len(groups))
    return sh * 2 + body


class FormatTests(unittest.TestCase):
    def test_data_sector_is_skipped(self):
        audio = sector(1, 0, 0x64, 0x00)
        data = sector(1, 0, 0x08, 0x00)
        out = parse(audio + data + audio)
        self.assertEqual(out["sectors"], 3)
        self.assertTrue(out["complete"])
        self.assertEqual(len(out["streams"]), 1)
        self.assertEqual(out["streams"][0]["sectors"], 2)

    def test_stereo_is_split_per_channel(self):
        # 4-bit stereo: even units left, odd units right; zero data -> silence
        out = parse(sector(2, 3, 0x64, 0x01))
        st = out["streams"][0]
        self.assertTrue(st["stereo"])
        self.assertEqual(st["bits"], 4)
        self.assertEqual(st["sample_rate"], 37800)
        # 18 groups * 8 units * 28 samples total (half on each channel).
        self.assertEqual(st["samples"], 18 * 8 * 28)
        self.assertEqual(st["duration"], "8/75")
        self.assertIsNotNone(st["last_right"])

    def test_half_rate_4bit_mono(self):
        out = parse(sector(1, 1, 0x64, 0x04))
        st = out["streams"][0]
        self.assertFalse(st["stereo"])
        self.assertEqual(st["sample_rate"], 18900)
        self.assertEqual(st["bits"], 4)
        self.assertEqual(st["samples"], 18 * 8 * 28)
        self.assertEqual(st["duration"], "16/75")
        self.assertIsNone(st["last_right"])

    def test_8bit_mono(self):
        out = parse(sector(1, 2, 0x64, 0x14))
        st = out["streams"][0]
        self.assertEqual(st["bits"], 8)
        self.assertEqual(st["sample_rate"], 18900)
        # 4 units of 28 whole-byte samples per group.
        self.assertEqual(st["samples"], 18 * 4 * 28)
        self.assertEqual(st["duration"], "8/75")

    def test_stream_order_is_first_appearance(self):
        a = sector(5, 0, 0x64, 0x00)
        b = sector(6, 0, 0x64, 0x00)
        out = parse(b + a + b)
        self.assertEqual([s["stream"] for s in out["streams"]], ["6/0", "5/0"])
        self.assertEqual(out["streams"][0]["sectors"], 2)

    def test_trailing_partial_file(self):
        out = parse(sector(1, 0, 0x64, 0x00) + b"\\x00" * 10)
        self.assertEqual(out["sectors"], 1)
        self.assertFalse(out["complete"])


if __name__ == "__main__":
    unittest.main()
