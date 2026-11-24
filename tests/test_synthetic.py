import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttxkit.ttx import parse, _ETSI_WORDS, _ALTERNATE_WORDS, TtxError

INV = {d: b for b, d in _ETSI_WORDS.items()}
AINV = {d: b for b, d in _ALTERNATE_WORDS.items()}


def ham_etsi(n):
    return INV[n & 15]


def ham_alt(n):
    return AINV[n & 15]


def header_packet(encoder, magazine, units, tens, sub=(0, 0, 0, 0), c7=0, c11=1, text=b""):
    p = bytearray(42)
    p[0] = encoder(magazine if magazine != 8 else 0)
    p[1] = encoder(0)
    s1, s2, s3, s4 = sub
    for i, v in enumerate([units, tens, s1, s2, s3, s4, c7, c11]):
        p[2 + i] = encoder(v)
    for i, ch in enumerate(text[:32]):
        p[10 + i] = ch
    for i in range(10 + len(text), 42):
        p[i] = 0x20
    return bytes(p)


def row_packet(encoder, magazine, row, fill=0x20):
    p = bytearray(42)
    hi = (0x8 if row >= 16 else 0) | (magazine if magazine != 8 else 0)
    p[0] = encoder(hi)
    p[1] = encoder(row & 15)
    for i in range(2, 42):
        p[i] = fill
    return bytes(p)


class SyntheticTests(unittest.TestCase):
    def test_standard_etsi_convention(self):
        data = header_packet(ham_etsi, 3, 2, 0xA, text=bytes([0xC8, 0x49]))
        data += row_packet(ham_etsi, 3, 1)
        data += row_packet(ham_etsi, 3, 25)
        r = parse(data)
        self.assertEqual(r["pages"][0]["page"], "3A2")
        self.assertEqual(r["magazines"], [3])
        self.assertEqual(r["display_rows"], 1)
        self.assertEqual(r["extension_packets"], 1)
        self.assertEqual(r["corrected"], 0)
        self.assertEqual(r["hamming_errors"], 0)
        self.assertEqual(r["header_text"], "HI")

    def test_magazine_eight(self):
        r = parse(header_packet(ham_etsi, 8, 8, 8))
        self.assertEqual(r["pages"][0]["page"], "888")
        self.assertEqual(r["magazines"], [8])

    def test_correctable_hamming(self):
        p = bytearray(header_packet(ham_etsi, 3, 2, 0xA))
        p[0] ^= 0x04
        r = parse(bytes(p))
        self.assertEqual(r["corrected"], 1)
        self.assertEqual(r["hamming_errors"], 0)
        self.assertEqual(r["pages"][0]["page"], "3A2")

    def test_uncorrectable_hamming(self):
        p = bytearray(header_packet(ham_etsi, 3, 2, 0xA))
        p[0] ^= 0x03
        r = parse(bytes(p))
        self.assertEqual(r["hamming_errors"], 1)
        self.assertEqual(r["pages"], [])

    def test_parity_error_shown_as_space(self):
        # 0x49 = "I" with correct odd parity (3 ones); 0xC9 flips bit 7.
        good = bytearray(header_packet(ham_etsi, 3, 2, 0xA, text=bytes([0x49, 0x49])))
        bad = bytearray(good)
        bad[10] = 0xC9  # 4 ones: even parity, a parity error
        r = parse(bytes(bad))
        self.assertEqual(r["parity_errors"], 1)
        self.assertEqual(r["header_text"], " I")
        self.assertEqual(parse(bytes(good))["parity_errors"], 0)

    def test_time_filler_header_excluded(self):
        r = parse(header_packet(ham_etsi, 1, 0xF, 0xF))
        self.assertIsNone(r["header_text"])
        self.assertEqual(r["pages"][0]["page"], "1FF")

    def test_newsflash_and_subtitle_bits(self):
        r = parse(header_packet(ham_etsi, 1, 0, 0, sub=(0, 0, 0, 0xC)))
        pg = r["pages"][0]
        self.assertTrue(pg["newsflash"])
        self.assertTrue(pg["subtitle"])

    def test_alternate_convention(self):
        data = header_packet(ham_alt, 8, 8, 8, sub=(0, 0, 0, 8))
        data += row_packet(ham_alt, 8, 20)
        r = parse(data)
        self.assertEqual(r["pages"][0]["page"], "888")
        self.assertTrue(r["pages"][0]["subtitle"])
        self.assertFalse(r["pages"][0]["newsflash"])
        self.assertEqual(r["magazines"], [8])

    def test_corrupted_header_byte_skips_page(self):
        p = bytearray(header_packet(ham_etsi, 3, 2, 0xA))
        p[5] ^= 0x03  # two-bit, uncorrectable error in a header Hamming byte
        r = parse(bytes(p))
        self.assertEqual(r["hamming_errors"], 1)
        self.assertEqual(r["pages"], [])
        self.assertEqual(r["display_rows"], 0)

    def test_subcode_and_remaining_control_bits(self):
        # S1=1, S2 carries erase (bit3) plus subpage bits, S3=5, S4=2
        r = parse(header_packet(ham_etsi, 2, 0x6, 0x3, sub=(1, 0xB, 5, 2), c7=1, c11=0))
        pg = r["pages"][0]
        self.assertEqual(pg["page"], "236")
        self.assertEqual(pg["subcode"], "2531")
        self.assertTrue(pg["erase"])
        self.assertTrue(pg["suppress_header"])
        self.assertFalse(pg["magazine_serial"])

    def test_bad_size(self):
        with self.assertRaises(TtxError):
            parse(bytes(50))
