import unittest

from nsfkit.nsf import NsfError, parse

from rips import auth, chunk, info, nsf, nsfe, times, tlbl


class BasicTests(unittest.TestCase):
    def test_nsf_header(self):
        out = parse(nsf(songs=8, title="Overworld"))
        self.assertEqual((out["format"], out["songs"], out["title"], out["load"]), ("NSF", 8, "Overworld", "8000"))

    def test_nsfe_auth(self):
        out = parse(nsfe(info(songs=4), auth("Racer", "Team")))
        self.assertEqual((out["format"], out["title"], out["artist"]), ("NSFe", "Racer", "Team"))


class ChipTests(unittest.TestCase):
    def test_chip_bit_order(self):
        self.assertEqual(parse(nsf(chips=0b0000001))["chips"], ["VRC6"])
        self.assertEqual(parse(nsf(chips=0b0000010))["chips"], ["VRC7"])
        self.assertEqual(parse(nsf(chips=0b0000011))["chips"], ["VRC6", "VRC7"])
        self.assertEqual(parse(nsf(chips=0b0000100))["chips"], ["FDS"])
        self.assertEqual(parse(nsfe(info(chips=0b0000011)))["chips"], ["VRC6", "VRC7"])


class RegionRateTests(unittest.TestCase):
    def test_ntsc(self):
        out = parse(nsf(region=0))
        self.assertEqual(out["region"], "NTSC")
        self.assertEqual(out["play_rate_hz"], "1000000/16639")

    def test_pal_uses_pal_speed(self):
        out = parse(nsf(region=1))
        self.assertEqual(out["region"], "PAL")
        self.assertEqual(out["play_rate_hz"], "1000000/19997")

    def test_dual_region(self):
        out = parse(nsf(region=3))
        self.assertEqual(out["region"], "dual")
        self.assertEqual(out["play_rate_hz"], "1000000/16639")

    def test_zero_pal_speed_falls_back_to_ntsc(self):
        out = parse(nsf(region=1, ntsc=16639, pal=0))
        self.assertEqual(out["play_rate_hz"], "1000000/16639")

    def test_nsfe_default_rates(self):
        self.assertEqual(parse(nsfe(info(region=0)))["play_rate_hz"], "1000000/16639")
        self.assertEqual(parse(nsfe(info(region=1)))["play_rate_hz"], "1000000/19997")
        self.assertEqual(parse(nsfe(info(region=3)))["region"], "dual")


class TextTests(unittest.TestCase):
    def test_garbage_after_nul_stripped(self):
        out = parse(nsf(title="Real Name\x00junk"))
        self.assertEqual(out["title"], "Real Name")

    def test_empty_fields_are_none(self):
        out = parse(nsf(title="", artist="", copyright=""))
        self.assertEqual((out["title"], out["artist"], out["copyright"]), (None, None, None))

    def test_nsfe_auth_empty_fields_are_none(self):
        out = parse(nsfe(info(), auth("", "Artist", "")))
        self.assertEqual((out["title"], out["artist"], out["copyright"]), (None, "Artist", None))

    def test_tlbl_drops_terminator_and_caps_at_song_count(self):
        out = parse(nsfe(info(songs=3), tlbl("A", "B", "C")))
        self.assertEqual(out["track_labels"], ["A", "B", "C"])


class NsfeSongNumberingTests(unittest.TestCase):
    def test_start_song_is_one_based(self):
        self.assertEqual(parse(nsfe(info(songs=4, start=0)))["start_song"], 1)
        self.assertEqual(parse(nsfe(info(songs=4, start=3)))["start_song"], 4)
        self.assertEqual(parse(nsfe(info(songs=4, start=None)))["start_song"], 1)

    def test_truncated_info_chunk(self):
        self.assertEqual(parse(nsfe(info(songs=None)))["songs"], 1)

    def test_playlist_is_one_based(self):
        out = parse(nsfe(info(), chunk("plst", bytes([0, 2, 4]))))
        self.assertEqual(out["playlist"], [1, 3, 5])


class TimeTests(unittest.TestCase):
    def test_negative_time_is_unknown(self):
        out = parse(nsfe(info(songs=3), times(1000, -1, 2500)))
        self.assertEqual(out["track_times_ms"], [1000, None, 2500])


class UnknownChunkTests(unittest.TestCase):
    def test_lowercase_chunk_skipped(self):
        out = parse(nsfe(info(), chunk("xtra", b"ignored")))
        self.assertEqual(out["format"], "NSFe")

    def test_uppercase_chunk_is_error(self):
        with self.assertRaises(NsfError):
            parse(nsfe(info(), chunk("XTRO", b"bad")))

    def test_missing_info_or_data_is_error(self):
        with self.assertRaises(NsfError):
            parse(b"NSFE" + chunk("NEND", b""))


class BankTests(unittest.TestCase):
    def test_nsf_any_bank_byte(self):
        self.assertFalse(parse(nsf())["bankswitched"])
        h = bytearray(nsf())
        h[0x74] = 1
        self.assertTrue(parse(bytes(h))["bankswitched"])

    def test_nsfe_bank_chunk(self):
        self.assertTrue(parse(nsfe(info(), chunk("BANK", bytes([0, 0, 3] + [0] * 5))))["bankswitched"])


class Nsf2Tests(unittest.TestCase):
    def test_nsf2_parse(self):
        import struct
        from rips import pad
        h = b"NESM\x1a" + struct.pack("<BBBHHH", 2, 3, 1, 0x8000, 0x8003, 0x8006)
        h += pad("T") + pad("A") + pad("C") + struct.pack("<H", 16639) + bytes(8)
        h += struct.pack("<H", 19997) + bytes([0, 0, 0]) + (100).to_bytes(3, "little") + bytes(20)
        out = parse(h + bytes(100))
        self.assertEqual(out["version"], 2)
        self.assertEqual(out["data_bytes"], len(h) - 128 + 100)


if __name__ == "__main__":
    unittest.main()
