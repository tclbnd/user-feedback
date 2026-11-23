import struct
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


class VersionTests(unittest.TestCase):
    def test_v0_headers_start_after_0x40(self):
        out = parse(fsb([(0, 0, 250, [])], [256], version=0, codec=2, names=["v0"]))
        s = out["samples"][0]
        self.assertEqual((out["version"], s["name"], s["sample_rate"], s["samples"]), (0, "v0", 4000, 250))

    def test_v1_headers_start_after_0x3c(self):
        out = parse(fsb([(0, 0, 250, [])], [256], version=1, codec=2))
        self.assertEqual(out["samples"][0]["samples"], 250)


class RateTests(unittest.TestCase):
    def test_rate_codes(self):
        table = [4000, 8000, 11000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000]
        for code, rate in enumerate(table):
            s = parse(fsb([(code, 1, 100, [])], [4096], version=1, codec=15))["samples"][0]
            self.assertEqual(s["sample_rate"], rate, code)

    def test_frequency_extra_wins(self):
        s = parse(fsb([(8, 1, 100, [(2, struct.pack("<I", 96000))])], [4096], version=1))["samples"][0]
        self.assertEqual(s["sample_rate"], 96000)


class ChannelTests(unittest.TestCase):
    def test_channel_codes(self):
        for code, channels in enumerate((1, 2, 6, 8)):
            s = parse(fsb([(8, code, 100, [])], [65536], version=1, codec=15))["samples"][0]
            self.assertEqual(s["channels"], channels, code)

    def test_channels_extra_wins(self):
        s = parse(fsb([(8, 0, 100, [(1, struct.pack("<B", 6))])], [65536], version=1))["samples"][0]
        self.assertEqual(s["channels"], 6)


class DataSizeTests(unittest.TestCase):
    def test_offsets_are_32_byte_units(self):
        out = parse(fsb([(8, 0, 100, []), (8, 0, 100, []), (8, 0, 100, [])], [100, 200, 64],
                        version=1, codec=15))
        self.assertEqual([s["data_bytes"] for s in out["samples"]], [128, 224, 64])
        self.assertEqual(sum(s["data_bytes"] for s in out["samples"]), out["data_bytes"])

    def test_padding_uses_sample_channels(self):
        s = parse(fsb([(8, 1, 100, [])], [1024], version=1, codec=2))["samples"][0]
        self.assertEqual(s["padding_bytes"], 1024 - 100 * 2 * 2)


class ExtraTests(unittest.TestCase):
    def test_chained_extras_keep_loop(self):
        extras = [(13, struct.pack("<f", 0.8)), (3, struct.pack("<II", 100, 999))]
        s = parse(fsb([(8, 1, 1000, extras)], [4096], version=1))["samples"][0]
        self.assertEqual(s["extras"], ["peakvolume", "loop"])
        self.assertEqual(s["loop"], {"start": 100, "end": 999})
        self.assertEqual((s["peak"], s["loop_ok"]), ("0.800", True))

    def test_unknown_extra_name(self):
        s = parse(fsb([(8, 1, 100, [(5, b"abc")])], [4096], version=1))["samples"][0]
        self.assertEqual(s["extras"], ["type_5"])


if __name__ == "__main__":
    unittest.main()
