import struct
import unittest

from fsbkit.fsb import FsbError, parse

from banks import fsb


class BasicTests(unittest.TestCase):
    def test_bank(self):
        out = parse(fsb([(8, 1, 1000, [])], [4096], version=0, codec=2, names=["click"]))
        self.assertEqual((out["codec"], out["sample_count"], out["samples"][0]["name"]), ("pcm16", 1, "click"))

    def test_mono(self):
        s = parse(fsb([(8, 0, 500, [])], [1024], version=0, codec=1))["samples"][0]
        self.assertEqual((s["channels"], s["samples"]), (1, 500))


class VersionTests(unittest.TestCase):
    def test_version_1_header(self):
        # Version 1 sample headers start at 0x3C, four bytes before version 0.
        out = parse(fsb([(8, 1, 1000, [])], [4096], version=1, codec=2, names=["click"]))
        self.assertEqual((out["version"], out["samples"][0]["name"]), (1, "click"))

    def test_version_0_header(self):
        out = parse(fsb([(8, 1, 1000, [])], [4096], version=0, codec=2, names=["click"]))
        self.assertEqual((out["version"], out["samples"][0]["name"]), (0, "click"))

    def test_both_versions_agree(self):
        banks = [parse(fsb([(8, 1, 1000, [])], [4096], version=v, codec=2)) for v in (0, 1)]
        self.assertEqual(banks[0]["samples"], banks[1]["samples"])


class FieldTests(unittest.TestCase):
    def test_rate_table_includes_4000(self):
        # Index 8 is 44100, index 9 is 48000; a table missing 4000 reports both one slot early.
        s = parse(fsb([(8, 1, 44100, [])], [4096]))["samples"][0]
        self.assertEqual(s["sample_rate"], 44100)
        s = parse(fsb([(9, 1, 48000, [])], [4096]))["samples"][0]
        self.assertEqual(s["sample_rate"], 48000)

    def test_surround_channels(self):
        # Channel codes are 1, 2, 6, 8; 2 means 5.1, not 3.
        self.assertEqual([parse(fsb([(8, c, 10, [])], [64]))["samples"][0]["channels"] for c in range(4)],
                         [1, 2, 6, 8])

    def test_data_offsets_are_32_byte_units(self):
        sizes = [1000, 3000]
        out = parse(fsb([(8, 1, 100, []), (8, 1, 200, [])], sizes))
        self.assertEqual([s["data_bytes"] for s in out["samples"]], [1024, 3008])
        self.assertEqual(sum(s["data_bytes"] for s in out["samples"]), out["data_bytes"])

    def test_duration_uses_sample_rate(self):
        s = parse(fsb([(8, 1, 44100, [])], [4096]))["samples"][0]
        self.assertEqual(s["duration"], "1")


class ExtraChunkTests(unittest.TestCase):
    def test_loop_after_other_chunk(self):
        # Extra chunks form a chain: the loop follows a Vorbis chunk here.
        extras = [(13, struct.pack("<f", 0.8)), (3, struct.pack("<II", 1000, 2000))]
        s = parse(fsb([(8, 1, 4000, extras)], [4096]))["samples"][0]
        self.assertEqual(s["loop"], {"start": 1000, "end": 2000})
        self.assertEqual(s["extras"], [13, 3])

    def test_chunk_channels_override(self):
        s = parse(fsb([(8, 0, 100, [(1, bytes([2]))])], [4096]))["samples"][0]
        self.assertEqual(s["channels"], 2)

    def test_chunk_rate_overrides_header(self):
        s = parse(fsb([(8, 1, 100, [(2, struct.pack("<I", 96000))])], [4096]))["samples"][0]
        self.assertEqual(s["sample_rate"], 96000)

    def test_loop_before_other_chunk(self):
        extras = [(3, struct.pack("<II", 10, 20)), (13, struct.pack("<f", 0.5))]
        s = parse(fsb([(8, 1, 4000, extras)], [4096]))["samples"][0]
        self.assertEqual((s["loop"], s["extras"]), ({"start": 10, "end": 20}, [3, 13]))

    def test_no_loop_without_chunk(self):
        self.assertIsNone(parse(fsb([(8, 1, 100, [])], [4096]))["samples"][0]["loop"])


class LongSampleTests(unittest.TestCase):
    def test_sample_count_above_24_bits(self):
        # The 30-bit sample count must not be clipped to 24 bits.
        n = (1 << 24) + 12345
        s = parse(fsb([(8, 1, n, [])], [4096]))["samples"][0]
        self.assertEqual(s["samples"], n)


class NameTests(unittest.TestCase):
    def test_without_name_table(self):
        out = parse(fsb([(8, 1, 10, []), (8, 1, 10, [])], [64, 64], names=None))
        self.assertEqual([s["name"] for s in out["samples"]], [None, None])


class TruncationTests(unittest.TestCase):
    def test_truncated(self):
        bank = fsb([(8, 1, 100, [])], [4096])
        out = parse(bank[:-100])
        self.assertTrue(out["truncated"])

    def test_not_fsb5(self):
        with self.assertRaises(FsbError):
            parse(b"RIFF" + bytes(100))

    def test_header_sizes_past_end(self):
        bank = bytearray(fsb([(8, 1, 100, [])], [4096], version=1))
        bank[0x0C:0x10] = struct.pack("<I", 0x10000)  # sample header size far beyond the file
        with self.assertRaises(FsbError):
            parse(bytes(bank))


class ExampleTests(unittest.TestCase):
    def _bank(self, name):
        with open("examples/" + name, "rb") as fh:
            return parse(fh.read())

    def test_music(self):
        out = self._bank("music.fsb")
        title, battle = out["samples"]
        self.assertEqual((title["name"], title["sample_rate"], title["channels"]), ("bgm_title", 44100, 2))
        self.assertEqual(title["loop"], {"start": 220500, "end": 4189499})
        self.assertEqual((battle["name"], battle["sample_rate"], battle["channels"]), ("bgm_battle_51", 48000, 6))
        self.assertEqual([s["data_bytes"] for s in out["samples"]], [900000, 1500000])

    def test_ui(self):
        out = self._bank("ui.fsb")
        self.assertEqual([s["name"] for s in out["samples"]], ["ui_ok", "ui_cancel", "ui_open"])
        self.assertEqual([s["sample_rate"] for s in out["samples"]], [22050, 22050, 32000])
        self.assertEqual([s["data_bytes"] for s in out["samples"]], [22080, 11040, 64000])
