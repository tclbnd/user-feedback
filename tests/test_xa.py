import struct
import unittest

from xakit.xa import XaError, parse

from rips import cdxa, sector, vcd


class LbaTests(unittest.TestCase):
    def test_lead_in_removed(self):
        # MSF 00:02:00 is LBA 0, not 150.
        out = parse(cdxa(vcd(0, 4)))
        self.assertEqual((out["start_lba"], out["end_lba"]), (0, 3))
        self.assertEqual(out["duration"], "4/75")

    def test_bcd_minutes_and_seconds(self):
        # LBA 750 -> MSF 00:12:00 (12 and 10 are BCD, not raw bytes).
        sectors = [sector(750 + i) for i in range(4)]
        out = parse(cdxa(sectors))
        self.assertEqual((out["start_lba"], out["end_lba"]), (750, 753))
        self.assertEqual(out["discontinuities"], 0)

    def test_bcd_minute_carry(self):
        # LBA 4500 -> MSF 01:02:00.
        out = parse(cdxa([sector(4500), sector(4501)]))
        self.assertEqual((out["start_lba"], out["end_lba"]), (4500, 4501))


class UserDataTests(unittest.TestCase):
    def test_form2_carries_2324_bytes(self):
        out = parse(cdxa(vcd(0, 10)))  # all form 2
        self.assertEqual(out["user_bytes"], 10 * 2324)

    def test_form1_carries_2048_bytes(self):
        # Form 1 data sector: submode 0x08 (data, form 1 bit clear).
        sectors = [sector(i, submode=0x08) for i in range(5)]
        out = parse(cdxa(sectors))
        self.assertEqual(out["form1"], 5)
        self.assertEqual(out["data"], 5)
        self.assertEqual(out["user_bytes"], 5 * 2048)

    def test_mode1_carries_2048_bytes(self):
        sectors = [sector(i, mode=1) for i in range(3)]
        out = parse(cdxa(sectors))
        self.assertEqual(out["modes"], {"1": 3})
        self.assertEqual(out["user_bytes"], 3 * 2048)
        self.assertEqual(out["edc_errors"], 0)


class EdcTests(unittest.TestCase):
    def test_valid_form2_edc_passes(self):
        out = parse(cdxa(vcd(0, 8)))
        self.assertEqual(out["edc_errors"], 0)

    def test_form2_zero_edc_is_not_an_error(self):
        sectors = [sector(0, edc_mode="zero"), sector(1, edc_mode="zero")]
        out = parse(cdxa(sectors))
        self.assertEqual(out["edc_errors"], 0)

    def test_form2_bad_edc_is_an_error(self):
        out = parse(cdxa([sector(0, edc_mode="bad")]))
        self.assertEqual(out["edc_errors"], 1)

    def test_form1_bad_edc_is_an_error(self):
        sectors = [sector(0, submode=0x20, edc_mode="bad")]
        out = parse(cdxa(sectors))
        self.assertEqual(out["edc_errors"], 1)

    def test_mode1_bad_edc_is_an_error(self):
        out = parse(cdxa([sector(0, mode=1, edc_mode="bad")]))
        self.assertEqual(out["edc_errors"], 1)


class BadSyncTests(unittest.TestCase):
    def test_bad_sync_counted_nowhere_else(self):
        sectors = [sector(0), sector(1, bad_sync=True), sector(2)]
        out = parse(cdxa(sectors))
        self.assertEqual(out["bad_sync"], 1)
        self.assertEqual(out["sectors"], 2)
        self.assertEqual(out["modes"], {"2": 2})
        self.assertEqual(out["form2"], 2)
        self.assertEqual(out["user_bytes"], 2 * 2324)
        self.assertEqual(out["start_lba"], 0)
        self.assertEqual(out["end_lba"], 2)

    def test_bad_sync_does_not_break_sequence(self):
        # The unreadable sector is skipped entirely; the next readable sector
        # only has to follow the last readable address.
        sectors = [sector(0), sector(1, bad_sync=True), sector(1)]
        out = parse(cdxa(sectors))
        self.assertEqual(out["discontinuities"], 0)
        self.assertEqual((out["start_lba"], out["end_lba"]), (0, 1))

    def test_gap_after_bad_sync_is_still_flagged(self):
        sectors = [sector(0), sector(1, bad_sync=True), sector(5)]
        out = parse(cdxa(sectors))
        self.assertEqual(out["discontinuities"], 1)

    def test_all_bad_sync(self):
        out = parse(cdxa([sector(0, bad_sync=True), sector(1, bad_sync=True)]))
        self.assertEqual((out["bad_sync"], out["sectors"]), (2, 0))
        self.assertIsNone(out["start_lba"])
        self.assertIsNone(out["duration"])


class SubheaderTests(unittest.TestCase):
    def test_subheader_mismatch(self):
        sectors = [sector(0), sector(1, sub_mismatch=True)]
        out = parse(cdxa(sectors))
        self.assertEqual(out["subheader_errors"], 1)


class InterleaveTests(unittest.TestCase):
    def test_interleaved_flag_big_endian(self):
        # Bit 13 of the big-endian wXAAttributes marks interleaved files.
        out = parse(cdxa(vcd(0, 4), attrs=0x2D55))
        self.assertTrue(out["interleaved"])

    def test_not_interleaved(self):
        out = parse(cdxa(vcd(0, 4), attrs=0x0D55))
        self.assertFalse(out["interleaved"])


class AudioFormatTests(unittest.TestCase):
    def _audio(self, coding):
        out = parse(cdxa([sector(0, submode=0x64, coding=coding)]))
        return out["audio_format"]

    def test_stereo_18900_4bit(self):
        # Exercise coding bits: 0x05 = stereo, 37.8/18.9 bit (0x04) set ->
        # 18.9 kHz, 4 bit ADPCM.
        self.assertEqual(self._audio(0x05),
                         {"channels": 2, "sample_rate": 18900,
                          "bits": 4, "emphasis": False})

    def test_mono_37800_4bit(self):
        # 0x00 = mono, rate bit clear -> 37.8 kHz.
        self.assertEqual(self._audio(0x00),
                         {"channels": 1, "sample_rate": 37800,
                          "bits": 4, "emphasis": False})

    def test_stereo_37800_8bit(self):
        self.assertEqual(self._audio(0x15)["bits"], 8)

    def test_emphasis(self):
        self.assertTrue(self._audio(0x45)["emphasis"])


class DiscontinuityTests(unittest.TestCase):
    def test_gap_between_readable_sectors(self):
        out = parse(cdxa([sector(0), sector(5), sector(6)]))
        self.assertEqual(out["discontinuities"], 1)


class RiffTests(unittest.TestCase):
    def test_trailing_bytes(self):
        out = parse(cdxa([sector(0)], trailing=b"\x00" * 7))
        self.assertEqual(out["sectors"], 1)
        self.assertEqual(out["trailing_bytes"], 7)

    def test_odd_chunk_padding_and_extra_chunk(self):
        # An odd-sized extra RIFF chunk carries one pad byte after it.
        extra = b"JUNK" + struct.pack("<I", 3) + b"abc" + b"\x00"
        out = parse(cdxa(vcd(0, 4), extra=extra))
        self.assertEqual(out["sectors"], 4)
        self.assertEqual(out["trailing_bytes"], 0)

    def test_not_a_cdxa_file(self):
        with self.assertRaises(XaError):
            parse(b"RIFF" + b"\x00" * 20)


class MixedRipTests(unittest.TestCase):
    def test_everything_together(self):
        sectors = [
            sector(100, submode=0x62),                    # form 2 video
            sector(101, bad_sync=True),                   # unreadable
            sector(101, submode=0x64, coding=0x05),       # form 2 audio
            sector(102, submode=0x08, sub_mismatch=True),  # form 1, bad subheader
            sector(110, submode=0x08, edc_mode="bad"),    # form 1 data, bad EDC
            sector(111, mode=1),                          # mode 1
        ]
        out = parse(cdxa(sectors, attrs=0x2D55, trailing=b"XY"))
        self.assertEqual(out["bad_sync"], 1)
        self.assertEqual(out["sectors"], 5)
        self.assertEqual((out["start_lba"], out["end_lba"]), (100, 111))
        self.assertEqual(out["discontinuities"], 1)       # 102 -> 110
        self.assertEqual(out["subheader_errors"], 1)
        self.assertEqual(out["edc_errors"], 1)
        self.assertEqual(out["trailing_bytes"], 2)
        self.assertTrue(out["interleaved"])
        self.assertEqual((out["form1"], out["form2"]), (2, 2))
        self.assertEqual(out["modes"], {"1": 1, "2": 4})
        self.assertEqual((out["video"], out["audio"], out["data"]), (1, 1, 2))
        self.assertEqual(out["user_bytes"], 2 * 2048 + 2 * 2324 + 2048)


class ExampleFilesTests(unittest.TestCase):
    def test_avseq(self):
        with open("examples/AVSEQ01.DAT", "rb") as fh:
            out = parse(fh.read())
        self.assertTrue(out["interleaved"])
        self.assertEqual((out["start_lba"], out["end_lba"]), (0, 59))
        self.assertEqual(out["sectors"], 60)
        self.assertEqual(out["form2"], 60)
        self.assertEqual(out["user_bytes"], 60 * 2324)
        self.assertEqual(out["edc_errors"], 0)
        self.assertEqual(out["bad_sync"], 0)
        self.assertEqual(out["discontinuities"], 0)
        self.assertEqual((out["video"], out["audio"]), (48, 12))

    def test_music(self):
        with open("examples/MUSIC01.DAT", "rb") as fh:
            out = parse(fh.read())
        self.assertFalse(out["interleaved"])
        self.assertEqual(out["xa_file_number"], 2)
        self.assertEqual((out["start_lba"], out["end_lba"]), (4500, 4535))
        self.assertEqual((out["form1"], out["form2"]), (6, 30))
        self.assertEqual(out["user_bytes"], 6 * 2048 + 30 * 2324)
        self.assertEqual(out["edc_errors"], 0)
        self.assertEqual(out["discontinuities"], 0)
        self.assertEqual(out["audio_format"],
                         {"channels": 2, "sample_rate": 18900,
                          "bits": 4, "emphasis": False})


if __name__ == "__main__":
    unittest.main()
