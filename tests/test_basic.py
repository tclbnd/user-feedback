import struct
import unittest
import zlib

from spreskit.spres import Spres, SpresError, parse


def packet(key_flag, code, raw):
    return bytes([0x60 | key_flag, code]) + zlib.compress(bytes(raw))


def pack(w, h, packets):
    out = b"SPRSPACK" + struct.pack("<HHH", w, h, len(packets))
    for p in packets:
        out += struct.pack("<I", len(p)) + p
    return out


def rgb(pic, w, x, y):
    o = (y * w + x) * 3
    return tuple(pic[o:o + 3])


class CodecTests(unittest.TestCase):
    def test_24bit_flip_bgr_swap_and_padding(self):
        # 3x1 BGR pixels per stored row -> 9 bytes, stride padded to 12.
        raw = bytearray(24)
        raw[0:3] = (10, 20, 30)       # stored bottom row -> display row 1
        raw[3:6] = (11, 21, 31)
        raw[6:9] = (12, 22, 32)
        raw[9:12] = (255, 255, 255)   # padding must never reach the picture
        raw[12:15] = (40, 50, 60)     # stored top row -> display row 0
        v = Spres(3, 2)
        v.packet(packet(1, 8, raw))
        self.assertEqual(v.cs, 3)
        pic = v.picture()
        self.assertEqual(rgb(pic, 3, 0, 0), (60, 50, 40))
        self.assertEqual(rgb(pic, 3, 0, 1), (30, 20, 10))
        self.assertEqual(rgb(pic, 3, 2, 1), (32, 22, 12))

    def test_padded_stride_does_not_shear(self):
        # width 5 * 3 = 15 bytes, padded to a 16-byte stride.
        w, h = 5, 4
        raw = bytearray(16 * h)
        for y in range(h):
            for x in range(w):
                raw[y * 16 + x * 3:y * 16 + x * 3 + 3] = (x, 100 + y, 200)
            raw[y * 16 + 15] = 126
        v = Spres(w, h)
        v.packet(packet(1, 8, raw))
        pic = v.picture()
        for y in range(h):
            for x in range(w):
                self.assertEqual(rgb(pic, w, x, y), (200, 100 + h - 1 - y, x))

    def test_32bit_pixel_code(self):
        raw = bytearray(8)
        raw[0:4] = (1, 2, 3, 4)       # B, G, R, unused
        raw[4:8] = (5, 6, 7, 8)
        v = Spres(2, 1)
        v.packet(packet(1, 16, raw))
        self.assertEqual(v.cs, 4)
        pic = v.picture()
        self.assertEqual(rgb(pic, 2, 0, 0), (3, 2, 1))
        self.assertEqual(rgb(pic, 2, 1, 0), (7, 6, 5))

    def test_rgb555_widens_components(self):
        v = Spres(1, 1)
        v.packet(packet(1, 4, struct.pack("<H", (31 << 10) | 7) + b"\0\0"))
        self.assertEqual(v.cs, 2)
        # 5-bit components are widened by repeating their top bits.
        self.assertEqual(tuple(v.picture()), (255, 0, 57))
        v = Spres(1, 1)
        v.packet(packet(1, 4, struct.pack("<H", 31 << 5) + b"\0\0"))
        self.assertEqual(tuple(v.picture()), (0, 255, 0))

    def test_delta_addition_is_modular(self):
        v = Spres(2, 1)
        v.packet(packet(1, 8, (250, 1, 2, 3, 4, 5, 0, 0)))
        v.packet(packet(0, 8, (10, 9, 0, 255, 1, 200, 0, 0)))
        pic = v.picture()
        self.assertEqual(rgb(pic, 2, 0, 0), (2, 10, 4))
        self.assertEqual(rgb(pic, 2, 1, 0), (205, 5, 2))

    def test_deltas_accumulate(self):
        v = Spres(1, 1)
        v.packet(packet(1, 8, (0, 0, 0, 0)))
        for _ in range(3):
            v.packet(packet(0, 8, (60, 60, 60, 0)))
        self.assertEqual(rgb(v.picture(), 1, 0, 0), (180, 180, 180))

    def test_delta_on_padded_stride(self):
        w, h = 5, 2
        key, delta = bytearray(32), bytearray(32)
        for y in range(h):
            for x in range(w):
                key[y * 16 + x * 3:y * 16 + x * 3 + 3] = (1, 2, 3)
                delta[y * 16 + x * 3:y * 16 + x * 3 + 3] = (4, 5, 6)
        v = Spres(w, h)
        v.packet(packet(1, 8, key))
        v.packet(packet(0, 8, delta))
        pic = v.picture()
        for y in range(h):
            for x in range(w):
                self.assertEqual(rgb(pic, w, x, y), (9, 7, 5))

    def test_delta_without_key_is_rejected(self):
        v = Spres(1, 1)
        with self.assertRaises(SpresError):
            v.packet(packet(0, 8, bytes(4)))
        self.assertIsNone(v.buf)
        v.packet(packet(1, 8, (7, 8, 9, 0)))
        self.assertEqual(tuple(v.picture()), (9, 8, 7))

    def test_bad_pixel_codes(self):
        v = Spres(1, 1)
        for code in (0, 1, 2, 3, 5, 7, 32):
            with self.assertRaises(SpresError):
                v.packet(packet(1, code, bytes(4)))

    def test_bad_frames_are_errors_and_decoder_recovers(self):
        good = packet(1, 8, bytes(4))
        bad_zlib = bytes((0x61, 8)) + b"junk"
        short = bytes((0x61, 8)) + zlib.compress(bytes(2))
        out = parse(pack(1, 1, [good, bad_zlib, short, good]))
        self.assertEqual(
            [f["state"] for f in out["frames"]],
            ["decoded", "error", "error", "decoded"],
        )

    def test_delta_after_error_still_uses_reference(self):
        key = packet(1, 8, bytes(4))
        delta = packet(0, 8, (1, 1, 1, 0))
        bad = bytes((0x60, 8)) + b"junk"
        out = parse(pack(1, 1, [key, bad, delta, delta]))
        self.assertEqual(
            [f["state"] for f in out["frames"]],
            ["decoded", "error", "decoded", "decoded"],
        )


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/tutorial.spr", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (40, 24, 3))
        self.assertEqual(
            [f["state"] for f in out["frames"]],
            ["decoded", "decoded", "decoded"],
        )
        self.assertEqual([f["key"] for f in out["frames"]], [True, False, False])
        self.assertEqual({f["bytes"] for f in out["frames"]}, {3})

    def test_bad_magic(self):
        with self.assertRaises(SpresError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
