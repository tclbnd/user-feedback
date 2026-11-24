import struct
import unittest

from pcxkit.pcx import decode, parse


def make_header(version=5, encoding=1, bits=8, xmin=0, ymin=0, xmax=1, ymax=0,
                planes=1, bpl=2, palinfo=1, ega=None):
    head = bytearray(128)
    head[0], head[1], head[2], head[3] = 0x0A, version, encoding, bits
    struct.pack_into("<6H", head, 4, xmin, ymin, xmax, ymax, 72, 72)
    if ega is not None:
        head[16:64] = ega
    head[65] = planes
    struct.pack_into("<HH", head, 66, bpl, palinfo)
    return bytes(head)


def vga_palette():
    pal = bytearray()
    for i in range(256):
        pal += bytes([i, 255 - i, (i * 7) & 0xFF])
    return bytes(pal)


class RleTests(unittest.TestCase):
    def test_0x80_to_0xbf_are_literals(self):
        stream = bytes([0x80, 0x90, 0xA0, 0xBF, 0xC2, 0x77, 0x05])
        out, _, runs = decode(stream, 0, 7, 1)
        self.assertEqual(out, bytes([0x80, 0x90, 0xA0, 0xBF, 0x77, 0x77, 0x05]))
        self.assertEqual(runs, 1)

    def test_runs_cross_plane_and_line_boundaries(self):
        # One run of 10 bytes spanning an 8-byte line, then literals.
        stream = bytes([0xCA, 0x55, 1, 2, 3, 4, 5, 6])
        out, _, runs = decode(stream, 0, 8, 2)
        self.assertEqual(out, bytes([0x55] * 10 + [1, 2, 3, 4, 5, 6]))
        self.assertEqual(runs, 1)

    def test_truncated_run_marker(self):
        out, _, runs = decode(bytes([0xC5]), 0, 4, 1)
        self.assertEqual(out, b"")
        self.assertEqual(runs, 0)

    def test_output_clamped_to_wanted_size(self):
        out, _, runs = decode(bytes([0xFF, 0x11]), 0, 4, 1)
        self.assertEqual(out, b"\x11\x11\x11\x11")
        self.assertEqual(runs, 1)


class HeaderTests(unittest.TestCase):
    def test_window_dimensions_are_inclusive(self):
        head = make_header(xmax=319, ymax=39, bpl=320)
        out = parse(head + b"\x00" * (320 * 40))
        self.assertEqual((out["width"], out["height"]), (320, 40))

    def test_padding_bits(self):
        # 12 pixel wide 1-bit plane stored in 2 bytes per plane.
        head = make_header(bits=1, planes=4, bpl=2, xmax=11, ymax=15)
        out = parse(head + b"\x00" * (8 * 16))
        self.assertEqual(out["padding_bits"], 4)


class MonoTests(unittest.TestCase):
    def test_corner_colors(self):
        head = make_header(version=3, encoding=0, bits=1, planes=1, bpl=1,
                           xmax=0, ymax=0)
        self.assertEqual(parse(head + b"\x00")["corner_pixel"], "#000000")
        self.assertEqual(parse(head + b"\x80")["corner_pixel"], "#ffffff")


class EgaTests(unittest.TestCase):
    def setUp(self):
        self.ega = bytearray(48)
        for i in range(16):
            self.ega[3 * i:3 * i + 3] = bytes([i * 16, i * 8, i * 4])

    def test_plane_zero_is_low_index_bit(self):
        # Top bit set in planes 0 and 2 -> index = bit0 | bit2 = 5.
        planar = bytes([0x80, 0x00, 0x80, 0x00])
        for version in (2, 3, 5):
            head = make_header(version=version, encoding=0, bits=1, planes=4,
                               bpl=1, xmax=0, ymax=0, ega=bytes(self.ega))
            out = parse(head + planar)
            self.assertEqual(out["palette"], "ega16")
            expect = "#%02x%02x%02x" % (self.ega[15], self.ega[16], self.ega[17])
            self.assertEqual(out["corner_pixel"], expect)

    def test_packed_pixel_uses_high_nibble(self):
        head = make_header(version=2, encoding=0, bits=4, planes=1, bpl=1,
                           xmax=0, ymax=0, ega=bytes(self.ega))
        out = parse(head + b"\xA0")
        expect = "#%02x%02x%02x" % (self.ega[30], self.ega[31], self.ega[32])
        self.assertEqual(out["corner_pixel"], expect)


class VgaTests(unittest.TestCase):
    def test_palette_components_not_scaled(self):
        pal = vga_palette()
        head = make_header(version=5, encoding=0, bits=8, planes=1, bpl=1,
                           xmax=0, ymax=0)
        out = parse(head + bytes([5]) + b"\x0c" + pal)
        self.assertEqual(out["palette"], "vga256")
        expect = "#%02x%02x%02x" % (pal[15], pal[16], pal[17])
        self.assertEqual(out["corner_pixel"], expect)

    def test_marker_recognised_without_version_gate(self):
        head = make_header(version=3, encoding=0, bits=8, planes=1, bpl=1,
                           xmax=0, ymax=0)
        out = parse(head + b"\x00" + b"\x0c" + vga_palette())
        self.assertEqual(out["palette"], "vga256")

    def test_without_palette_is_none_and_corner_null(self):
        head = make_header(version=5, encoding=0, bits=8, planes=1, bpl=1,
                           xmax=0, ymax=0)
        out = parse(head + b"\x05")
        self.assertEqual(out["palette"], "none")
        self.assertIsNone(out["corner_pixel"])


class TruecolorTests(unittest.TestCase):
    def test_planes_hold_whole_component_lines(self):
        head = make_header(version=5, encoding=0, bits=8, planes=3, bpl=4,
                           xmax=3, ymax=0)
        body = bytes([10, 11, 12, 13]) + bytes([20, 21, 22, 23]) + \
            bytes([30, 31, 32, 33])
        out = parse(head + body)
        self.assertEqual(out["palette"], "truecolor")
        self.assertEqual(out["corner_pixel"], "#0a141e")


class CompletenessTests(unittest.TestCase):
    def test_truncated_stream_is_incomplete(self):
        # Three lines of four bytes wanted, only six bytes available.
        head = make_header(encoding=1, bits=8, planes=1, bpl=4, xmax=3, ymax=2)
        out = parse(head + bytes([0xC4, 0x11, 0x22, 0x33]))
        self.assertFalse(out["complete"])
        self.assertEqual(out["decoded_lines"], 1)

    def test_full_stream_is_complete(self):
        head = make_header(encoding=1, bits=8, planes=1, bpl=4, xmax=3, ymax=1)
        out = parse(head + bytes([0xC4, 0x11, 0xC4, 0x22]))
        self.assertTrue(out["complete"])
        self.assertEqual(out["decoded_lines"], 2)
        self.assertEqual(out["runs"], 2)


if __name__ == "__main__":
    unittest.main()
