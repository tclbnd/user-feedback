"""Spec conformance for the PSID/RSID header parser (versions 1-4)."""
import struct
import unittest

from sidkit.sid import parse


def header(version, load, init, play, songs, start, speed,
           flags=None, sid2=0, sid3=0, page=0, page_len=0):
    size = 0x7C if version >= 2 else 0x76
    h = bytearray(size)
    h[:4] = b"RSID" if flags is not None and flags & 2 else b"PSID"
    struct.pack_into(">HHHHHHHI", h, 4,
                     version, size, load, init, play, songs, start, speed)
    h[0x16:0x1A] = b"Test"
    if version >= 2:
        struct.pack_into(">H", h, 0x76, flags or 0)
        h[0x78], h[0x79] = page, page_len
        if version >= 3:
            h[0x7A] = sid2
            if version >= 4:
                h[0x7B] = sid3
    return bytes(h)


class HeaderTests(unittest.TestCase):
    def test_big_endian_addresses_and_song_count(self):
        out = parse(header(1, 0x1000, 0x1000, 0x1003, 19, 1, 0) + bytes(16))
        self.assertEqual(out["init_address"], "$1000")
        self.assertEqual(out["play_address"], "$1003")
        self.assertEqual((out["songs"], out["start_song"]), (19, 1))

    def test_v2_header_not_counted_as_data(self):
        out = parse(header(2, 0x1000, 0x1000, 0x1003, 1, 1, 0) + bytes(16))
        self.assertEqual(out["data_bytes"], 16)
        self.assertEqual(out["end_address"], "$100F")

    def test_embedded_load_address(self):
        body = struct.pack(">H", 0x2000) + bytes(14)
        out = parse(header(2, 0, 0, 0, 1, 1, 0) + body)
        self.assertEqual(out["load_address"], "$2000")
        self.assertEqual(out["data_bytes"], 14)
        self.assertEqual(out["end_address"], "$200D")
        self.assertEqual(out["init_address"], "$2000")

    def test_speed_bit_maps_to_song_number(self):
        # song 1 -> bit 0 ... song n -> bit n-1; bit 1 here marks song 2
        out = parse(header(1, 0x1000, 0x1000, 0x1003, 3, 1, 1 << 1) + bytes(8))
        self.assertEqual(out["speeds"], ["vbi", "cia", "vbi"])
        # a set bit 0 marks the first song, proving the bits are not reversed
        out = parse(header(1, 0x1000, 0x1000, 0x1003, 3, 1, 1 << 0) + bytes(8))
        self.assertEqual(out["speeds"], ["cia", "vbi", "vbi"])
        # bit 31 is reused for every song from 32 onwards
        out = parse(header(1, 0x1000, 0x1000, 0x1003, 40, 1, 1 << 31) + bytes(8))
        self.assertEqual(out["speeds"][:31], ["vbi"] * 31)
        self.assertEqual(out["speeds"][31:], ["cia"] * 9)

    def test_clock_and_sid_model_bits(self):
        out = parse(header(2, 0x1000, 0x1000, 0x1003, 1, 1, 0,
                           flags=(1 << 4) | (2 << 2)) + bytes(8))
        self.assertEqual((out["clock"], out["sid_model"]), ("PAL", ["8580"]))
        out = parse(header(2, 0x1000, 0x1000, 0x1003, 1, 1, 0,
                           flags=(2 << 4) | (1 << 2)) + bytes(8))
        self.assertEqual((out["clock"], out["sid_model"]), ("NTSC", ["6581"]))

    def test_v3_second_sid(self):
        out = parse(header(3, 0x1000, 0x1000, 0x1003, 1, 1, 0,
                           flags=1 << 10, sid2=0x50) + bytes(8))
        self.assertEqual(out["chips"], ["$D400", "$D500"])
        self.assertEqual(out["sid_model"], ["unknown", "6581"])
        out = parse(header(3, 0x1000, 0x1000, 0x1003, 1, 1, 0, sid2=0x41) + bytes(8))
        self.assertEqual(out["chips"], ["$D400"])
        self.assertEqual(out["sid_model"], ["unknown"])
        out = parse(header(2, 0x1000, 0x1000, 0x1003, 1, 1, 0, sid2=0x50) + bytes(8))
        self.assertEqual(out["chips"], ["$D400"])

    def test_v4_third_sid_model_bits_13_12(self):
        out = parse(header(4, 0x1000, 0x1000, 0x1003, 1, 1, 0,
                           flags=(1 << 10) | (2 << 12), sid2=0x50, sid3=0xFE) + bytes(8))
        self.assertEqual(out["chips"], ["$D400", "$D500", "$DFE0"])
        self.assertEqual(out["sid_model"], ["unknown", "6581", "8580"])

    def test_v1_has_no_v2_fields(self):
        out = parse(header(1, 0x1000, 0x1000, 0x1003, 1, 1, 0) + bytes(8))
        self.assertIsNone(out["clock"])
        self.assertIsNone(out["sid_model"])
        self.assertIsNone(out["free_pages"])
        self.assertEqual(out["chips"], ["$D400"])


if __name__ == "__main__":
    unittest.main()
