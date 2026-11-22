import struct
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
    def test_vrc6_is_bit_zero(self):
        self.assertEqual(parse(nsf(chips=0x01))["chips"], ["VRC6"])

    def test_vrc7_is_bit_one(self):
        self.assertEqual(parse(nsf(chips=0x02))["chips"], ["VRC7"])

    def test_all_chips_in_order(self):
        self.assertEqual(parse(nsf(chips=0x7F))["chips"],
                         ["VRC6", "VRC7", "FDS", "MMC5", "Namco163", "Sunsoft5B", "VT02"])

    def test_nsfe_chip_bits(self):
        self.assertEqual(parse(nsfe(info(chips=0x02)))["chips"], ["VRC7"])


class RateTests(unittest.TestCase):
    def test_ntsc_uses_ntsc_speed(self):
        self.assertEqual(parse(nsf(region=0, ntsc=16639, pal=19997))["play_rate_hz"], "1000000/16639")

    def test_pal_uses_pal_speed(self):
        self.assertEqual(parse(nsf(region=1, ntsc=16639, pal=19997))["play_rate_hz"], "1000000/19997")

    def test_dual_uses_ntsc_speed(self):
        self.assertEqual(parse(nsf(region=2, ntsc=16639, pal=19997))["play_rate_hz"], "1000000/16639")

    def test_nsfe_pal_rate(self):
        out = parse(nsfe(info(region=1)))
        self.assertEqual((out["region"], out["play_rate_hz"]), ("PAL", "50"))


class TextTests(unittest.TestCase):
    def test_title_stops_at_nul(self):
        raw = bytearray(nsf(title="Castle Quest"))
        raw[0x0E:0x2E] = b"Castle Quest\0jnk" + bytes(18)
        self.assertEqual(parse(bytes(raw))["title"], "Castle Quest")

    def test_empty_title_is_null(self):
        raw = bytearray(nsf(title=""))
        raw[0x0E:0x2E] = bytes(32)
        self.assertIsNone(parse(bytes(raw))["title"])

    def test_trailing_spaces_trimmed(self):
        raw = bytearray(nsf(title=""))
        raw[0x0E:0x2E] = b"Quest   " + bytes(24)
        self.assertEqual(parse(bytes(raw))["title"], "Quest")

    def test_null_terminated_auth_field(self):
        body = b"A\0B\0C\0"
        self.assertEqual(parse(nsfe(info(), chunk("auth", body)))["copyright"], "C")


class StartSongTests(unittest.TestCase):
    def test_nsfe_start_is_one_based(self):
        self.assertEqual(parse(nsfe(info(songs=5, start=0)))["start_song"], 1)
        self.assertEqual(parse(nsfe(info(songs=5, start=2)))["start_song"], 3)

    def test_nsf_zero_start_means_first(self):
        self.assertEqual(parse(nsf(start=0))["start_song"], 1)


class TrackTests(unittest.TestCase):
    def test_unknown_time_is_null(self):
        out = parse(nsfe(info(), times(1000, -1, 2000)))
        self.assertEqual(out["track_times_ms"], [1000, None, 2000])

    def test_times_are_signed(self):
        out = parse(nsfe(info(), chunk("time", struct.pack("<4i", 0, -1, 500, 2147483647))))
        self.assertEqual(out["track_times_ms"], [0, None, 500, 2147483647])

    def test_labels_do_not_gain_empty_entry(self):
        self.assertEqual(parse(nsfe(info(), tlbl("One", "Two")))["track_labels"], ["One", "Two"])

    def test_playlist_is_one_based(self):
        self.assertEqual(parse(nsfe(info(songs=3), chunk("plst", bytes([0, 2, 1]))))["playlist"], [1, 3, 2])


class ChunkTests(unittest.TestCase):
    def test_lower_case_unknown_chunk_skipped(self):
        out = parse(nsfe(chunk("xtra", b"private"), info(songs=2)))
        self.assertEqual(out["songs"], 2)

    def test_upper_case_unknown_chunk_is_error(self):
        with self.assertRaises(NsfError):
            parse(nsfe(chunk("Xtra", b"unknown"), info()))


class Nsf2Tests(unittest.TestCase):
    def test_program_length_excludes_appended_data(self):
        out = parse(nsf(version=2, prg=1000, appended=b"EXTRA!"))
        self.assertEqual(out["data_bytes"], 1000)

    def test_zero_program_length_means_to_end(self):
        out = parse(nsf(version=2, prg=1000))
        self.assertEqual(out["data_bytes"], 1000)
