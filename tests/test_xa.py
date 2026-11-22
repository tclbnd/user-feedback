import struct
import unittest

from xakit.xa import SYNC, SECTOR, parse

from rips import cdxa, edc, msf, sector


def riff(sectors, attrs=0x0D55, file_number=1, trailing=b""):
    data = b"".join(sectors) + trailing
    fmt = struct.pack("<HH", 0, 0) + struct.pack(">H", attrs) + b"XA" + bytes([file_number]) + bytes(5)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"CDXA" + body


def mode1(lba, bad=False):
    s = bytearray(SECTOR)
    s[0:12] = SYNC
    s[12:15] = msf(lba)
    s[15] = 1
    s[16:2064] = b"\x22" * 2048
    struct.pack_into("<I", s, 2064, edc(bytes(s[0:2064])) ^ (1 if bad else 0))
    return bytes(s)


def mode0(lba):
    s = bytearray(SECTOR)
    s[0:12] = SYNC
    s[12:15] = msf(lba)
    s[15] = 0
    return bytes(s)


def bad_sync(lba, submode=0x62):
    s = bytearray(sector(lba, submode))
    s[0:12] = bytes(12)
    return bytes(s)


class AddressTests(unittest.TestCase):
    def test_msf_is_lba_plus_150(self):
        # MSF 00:02:00 must be LBA 0, not 150.
        out = parse(riff([sector(0)]))
        self.assertEqual(out["start_lba"], 0)
        self.assertEqual(out["end_lba"], 0)

    def test_bcd_across_nine(self):
        # BCD 0x10 is ten; a run crossing 00:02:09/10 must stay contiguous.
        out = parse(riff([sector(8 + i) for i in range(12)]))
        self.assertEqual((out["start_lba"], out["end_lba"]), (8, 19))
        self.assertEqual(out["discontinuities"], 0)

    def test_bcd_across_minute(self):
        out = parse(riff([sector(4490 + i) for i in range(20)]))
        self.assertEqual((out["start_lba"], out["end_lba"]), (4490, 4509))
        self.assertEqual(out["discontinuities"], 0)
        self.assertEqual(out["duration"], "4/15")

    def test_discontinuity(self):
        out = parse(riff([sector(0), sector(5)]))
        self.assertEqual(out["discontinuities"], 1)


class UserDataTests(unittest.TestCase):
    def test_form2_carries_2324_bytes(self):
        out = parse(riff([sector(i, 0x62) for i in range(3)]))
        self.assertEqual(out["user_bytes"], 3 * 2324)

    def test_mixed_forms_and_mode1_mode0(self):
        out = parse(riff([sector(0, 0x62), sector(1, 0x62), sector(2, 0x40), mode1(3), mode0(4)]))
        self.assertEqual((out["form2"], out["form1"]), (2, 1))
        self.assertEqual(out["user_bytes"], 2 * 2324 + 2048 + 2048)
        self.assertEqual(out["modes"], {"2": 3, "1": 1, "0": 1})

    def test_trailing_bytes(self):
        out = parse(riff([sector(0), sector(1)], trailing=b"xyz"))
        self.assertEqual(out["sectors"], 2)
        self.assertEqual(out["trailing_bytes"], 3)


class EdcTests(unittest.TestCase):
    def test_valid_edcs_pass(self):
        out = parse(riff([sector(0, 0x62), sector(1, 0x40), mode1(2)]))
        self.assertEqual(out["edc_errors"], 0)

    def test_form2_zero_edc_means_not_computed(self):
        s = bytearray(sector(0, 0x62))
        struct.pack_into("<I", s, 2348, 0)
        self.assertEqual(parse(riff([bytes(s)]))["edc_errors"], 0)

    def test_form2_bad_edc_reported(self):
        s = bytearray(sector(0, 0x62))
        struct.pack_into("<I", s, 2348, struct.unpack_from("<I", s, 2348)[0] ^ 0xFF)
        self.assertEqual(parse(riff([bytes(s)]))["edc_errors"], 1)

    def test_form1_and_mode1_bad_edc_reported(self):
        f1 = bytearray(sector(0, 0x40))
        struct.pack_into("<I", f1, 2072, struct.unpack_from("<I", f1, 2072)[0] ^ 1)
        self.assertEqual(parse(riff([bytes(f1)]))["edc_errors"], 1)
        self.assertEqual(parse(riff([mode1(0, bad=True)]))["edc_errors"], 1)


class AudioTests(unittest.TestCase):
    def test_sample_rates_not_swapped(self):
        fast = parse(riff([sector(0, 0x64, 0x00)]))["audio_format"]
        slow = parse(riff([sector(0, 0x64, 0x04)]))["audio_format"]
        self.assertEqual(fast["sample_rate"], 37800)
        self.assertEqual(slow["sample_rate"], 18900)

    def test_coding_attributes(self):
        out = parse(riff([sector(0, 0x64, 0x55)]))["audio_format"]
        self.assertEqual(out, {"channels": 2, "sample_rate": 18900, "bits": 8, "emphasis": True})


class HeaderTests(unittest.TestCase):
    def test_interleaved_from_big_endian_attrs(self):
        self.assertTrue(parse(riff([sector(0)], attrs=0x2D55))["interleaved"])
        self.assertFalse(parse(riff([sector(0)], attrs=0x0D55))["interleaved"])

    def test_file_number(self):
        self.assertEqual(parse(riff([sector(0)], file_number=7))["xa_file_number"], 7)

    def test_subheader_mismatch(self):
        s = bytearray(sector(0, 0x62))
        s[20:24] = bytes([1, 1, 0x63, 0])
        out = parse(riff([bytes(s)]))
        self.assertEqual(out["subheader_errors"], 1)
        # Classification still follows the first subheader copy (0x62 = video).
        self.assertEqual(out["video"], 1)

    def test_channels(self):
        s = bytearray(sector(1))
        s[16], s[17], s[20], s[21] = 2, 3, 2, 3
        out = parse(riff([sector(0), bytes(s)]))
        self.assertEqual(out["channels"], {"1:1": 1, "2:3": 1})

    def test_records_and_eof(self):
        eor = bytearray(sector(0, 0x65))    # EOR
        eof1 = bytearray(sector(1, 0xE5))  # EOF + EOR
        eof2 = bytearray(sector(2, 0xE4))  # EOF only, still ends a record
        out = parse(riff([bytes(eor), bytes(eof1), bytes(eof2)]))
        self.assertEqual(out["records"], 3)
        self.assertEqual(out["eof_lba"], 1)


class BadSyncTests(unittest.TestCase):
    def test_bad_sync_counted_nowhere_else(self):
        out = parse(riff([bad_sync(0), sector(1)]))
        self.assertEqual((out["sectors"], out["bad_sync"]), (2, 1))
        self.assertEqual(out["modes"], {"2": 1})
        self.assertEqual(out["start_lba"], 1)
        self.assertEqual(out["end_lba"], 1)
        self.assertEqual(out["user_bytes"], 2324)
        self.assertEqual(out["edc_errors"], 0)
        self.assertEqual(out["channels"], {"1:1": 1})

    def test_bad_sync_does_not_make_discontinuity(self):
        out = parse(riff([sector(10), bad_sync(11), sector(12)]))
        self.assertEqual(out["bad_sync"], 1)
        self.assertEqual(out["discontinuities"], 0)

    def test_only_bad_sync_sectors(self):
        out = parse(riff([bad_sync(0)]))
        self.assertEqual(out["sectors"], 1)
        self.assertIsNone(out["start_lba"])
        self.assertIsNone(out["duration"])
        self.assertEqual(out["user_bytes"], 0)


class RiffTests(unittest.TestCase):
    def test_odd_chunk_padding_and_split_data(self):
        fmt = struct.pack("<HH", 0, 0) + struct.pack(">H", 0x0D55) + b"XA" + bytes([1]) + bytes(5)
        junk = b"JUNK" + struct.pack("<I", 3) + b"abc" + bytes(1)
        s0, s1 = sector(0), sector(1)
        body = (b"fmt " + struct.pack("<I", len(fmt)) + fmt + junk
                + b"data" + struct.pack("<I", SECTOR) + s0
                + b"data" + struct.pack("<I", SECTOR) + s1)
        out = parse(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"CDXA" + body)
        self.assertEqual(out["sectors"], 2)
        self.assertEqual(out["end_lba"], 1)


if __name__ == "__main__":
    unittest.main()
