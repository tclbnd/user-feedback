import unittest

from spckit.spc import SpcError, parse

from rips import spc


class BasicTests(unittest.TestCase):
    def test_tag(self):
        out = parse(spc(title="Opening", game="Quest"))
        self.assertEqual((out["title"], out["game"], out["id666_format"]), ("Opening", "Quest", "text"))

    def test_seconds(self):
        out = parse(spc(secs=95, emulator=1))
        self.assertEqual((out["play_seconds"], out["emulator"], out["pc"]), (95, "ZSNES", "0430"))


class TextLayoutTests(unittest.TestCase):
    def test_full_text_tag(self):
        out = parse(spc(title="Overworld", game="Star Quest", artist="Yoko S.", dumper="rip",
                        date=(1999, 3, 7), secs=150, fade=10000, emulator=2))
        self.assertEqual(out["id666_format"], "text")
        self.assertEqual(out["artist"], "Yoko S.")  # first letter must not be lost
        self.assertEqual(out["date"], "1999-03-07")  # MM/DD/YYYY normalised
        self.assertEqual(out["fade_ms"], 10000)  # five-digit fade field, not 100
        self.assertEqual(out["play_seconds"], 150)
        self.assertEqual(out["emulator"], "Snes9x")
        self.assertEqual(out["length_seconds"], "150")

    def test_empty_text_fields(self):
        out = parse(spc(title="", artist="", comments="", secs=0, fade=0, date=(0, 0, 0)))
        self.assertIsNone(out["title"])
        self.assertIsNone(out["artist"])
        self.assertIsNone(out["comments"])
        self.assertIsNone(out["date"])
        self.assertIsNone(out["play_seconds"])
        self.assertIsNone(out["fade_ms"])
        self.assertIsNone(out["length_seconds"])


class BinaryLayoutTests(unittest.TestCase):
    def test_full_binary_tag(self):
        out = parse(spc(layout="binary", title="Final Boss", artist="Yoko S.", date=(2003, 11, 20),
                        secs=210, fade=8000, emulator=1))
        self.assertEqual(out["id666_format"], "binary")
        self.assertEqual(out["title"], "Final Boss")
        self.assertEqual(out["artist"], "Yoko S.")  # binary artist starts at 0xB0
        self.assertEqual(out["date"], "2003-11-20")  # binary day/month/year, not garbled
        self.assertEqual(out["play_seconds"], 210)  # 3-byte little-endian
        self.assertEqual(out["fade_ms"], 8000)  # 4-byte little-endian
        self.assertEqual(out["emulator"], "ZSNES")  # emulator byte at 0xD1
        self.assertEqual(out["length_seconds"], "210")

    def test_binary_blank_numbers(self):
        out = parse(spc(layout="binary", secs=0, fade=0, date=(0, 0, 0)))
        self.assertIsNone(out["play_seconds"])
        self.assertIsNone(out["fade_ms"])
        self.assertIsNone(out["date"])


class NoTagTests(unittest.TestCase):
    def test_untagged_file_has_null_fields(self):
        out = parse(spc(tag=False))
        self.assertFalse(out["has_id666"])
        self.assertIsNone(out["id666_format"])
        # leftover RAM bytes must not be reported as a song title
        self.assertIsNone(out["title"])
        self.assertIsNone(out["artist"])
        self.assertIsNone(out["date"])
        self.assertIsNone(out["length_seconds"])
        self.assertEqual(out["muted_voices"], [])

    def test_bad_magic_rejected(self):
        with self.assertRaises(SpcError):
            parse(spc()[:0x10200].replace(b"SNES-SPC700", b"XXXXXXXX", 1))


class Xid6Tests(unittest.TestCase):
    def test_extended_title_overrides(self):
        data = spc(title="Final Boss", xid6=((0x01, 1, "Final Boss - Ascension of the Dark Star"),))
        out = parse(data)
        self.assertTrue(out["xid6"])
        self.assertEqual(out["title"], "Final Boss - Ascension of the Dark Star")

    def test_extended_fields_and_overrides(self):
        chunks = (
            (0x03, 1, "Another Artist"),       # artist overrides ID666
            (0x05, 1, "07/21/2005"),            # date overrides ID666
            (0x07, 1, "Original Soundtrack"),   # ost title
            (0x08, 1, "2"),                     # ost disc
            (0x09, 1, "7a"),                    # ost track
            (0x0A, 1, "Nintendo"),              # publisher
            (0x0B, 0, 1995),                    # copyright year (16-bit)
            (0x0D, 4, 5000),                    # fade override (32-bit)
            (0x12, 4, 1),                       # emulator override
        )
        out = parse(spc(xid6=chunks))
        self.assertEqual(out["artist"], "Another Artist")
        self.assertEqual(out["date"], "2005-07-21")
        self.assertEqual(out["ost_title"], "Original Soundtrack")
        self.assertEqual(out["ost_disc"], "2")
        self.assertEqual(out["ost_track"], "7a")
        self.assertEqual(out["publisher"], "Nintendo")
        self.assertEqual(out["copyright_year"], 1995)
        self.assertEqual(out["fade_ms"], 5000)
        self.assertEqual(out["emulator"], "ZSNES")

    def test_length_from_timing_chunks(self):
        # intro 11s + loop 20s x 2 + end 4s = 55s
        chunks = ((0x30, 4, 11000), (0x31, 4, 20000), (0x32, 4, 4000), (0x35, 0, 2))
        out = parse(spc(secs=180, xid6=chunks))
        self.assertEqual(out["length_seconds"], "55")

    def test_fractional_length(self):
        # 1.5s intro, no loop, 0.25s end -> 7/4
        chunks = ((0x30, 4, 1500), (0x32, 4, 250))
        out = parse(spc(xid6=chunks))
        self.assertEqual(out["length_seconds"], "7/4")

    def test_loop_count_defaults_to_zero(self):
        out = parse(spc(xid6=((0x30, 4, 10000), (0x31, 4, 5000))))
        self.assertEqual(out["length_seconds"], "10")  # loop contributes nothing

    def test_muted_voices_bitfield(self):
        # bits 0, 2 and 7 set -> voices [0, 2, 7]
        out = parse(spc(xid6=((0x0E, 4, (1 << 0) | (1 << 2) | (1 << 7)),)))
        self.assertEqual(out["muted_voices"], [0, 2, 7])

    def test_amplification_fraction(self):
        out = parse(spc(xid6=((0x33, 4, 0x8000),)))  # half of 0x10000
        self.assertEqual(out["amplification"], "1/2")

    def test_truncated_final_integer_chunk(self):
        # mirror of boss.spc: a 16-bit chunk whose two data bytes were cut off
        data = spc(xid6=((0x35, 0, 2),), truncate=2)
        out = parse(data)
        self.assertTrue(out["xid6"])
        self.assertEqual(out["length_seconds"], "180")  # still falls back to ID666 length

    def test_xid6_on_untagged_file(self):
        out = parse(spc(tag=False, xid6=((0x01, 1, "RAM Dump"), (0x30, 4, 30000))))
        self.assertFalse(out["has_id666"])
        self.assertTrue(out["xid6"])
        self.assertEqual(out["title"], "RAM Dump")
        self.assertEqual(out["length_seconds"], "30")


if __name__ == "__main__":
    unittest.main()
