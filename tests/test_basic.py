import os
import struct
import unittest

from xwdkit.xwd import XwdError, parse

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "examples")


def header(w, h, bpp, visual=3):
    return struct.pack(">25I", 100, 7, 2, bpp, w, h, 0, 1, 32, 1, 32, bpp, w * bpp // 8,
                       visual, 0, 0, 0, 8, 0, 0, w, h, 0, 0, 0)


def make_header(w, h, bpp, visual=3, bpl=None, name=None, ncolors=0, masks=(0, 0, 0),
                byte_order=1, bit_order=1, order=">", version=7):
    """Serialise a version 7 header, followed by the window name padding."""
    if bpl is None:
        bpl = w * bpp // 8
    tail = b""
    head_size = 100
    if name is not None:
        raw = name.encode("latin-1") + b"\0"
        raw += b"\0" * (-len(raw) % 4)
        head_size += len(raw)
        tail = raw
    fields = (head_size, version, 2, bpp, w, h, 0, byte_order, 32, bit_order, 32, bpp, bpl,
              visual, masks[0], masks[1], masks[2], 8, 0, ncolors, w, h, 0, 0, 0)
    return struct.pack(order + "25I", *fields) + tail


def colour_map(entries, order=">"):
    # XWDColor: pixel, red, green, blue, flags, pad - 12 bytes per entry.
    return b"".join(struct.pack(order + "IHHHBB", *e, 7, 0) for e in entries)


class BasicTests(unittest.TestCase):
    def test_size(self):
        out = parse(header(4, 2, 8) + bytes(8))
        self.assertEqual((out["width"], out["height"], out["image_bytes"]), (4, 2, 8))

    def test_version(self):
        data = bytearray(header(4, 2, 8) + bytes(8))
        data[7] = 6
        with self.assertRaises(XwdError):
            parse(bytes(data))


class ExampleTests(unittest.TestCase):
    def load(self, name):
        with open(os.path.join(EXAMPLES, name), "rb") as fh:
            return parse(fh.read())

    def test_pseudo_color_with_window_name(self):
        out = self.load("xterm.xwd")
        self.assertEqual(out["window_name"], "xterm")
        self.assertEqual(out["colors"], 16)
        self.assertEqual(out["points"]["top_left"], "#00ff00")

    def test_true_color_with_padded_rows(self):
        out = self.load("desktop.xwd")
        self.assertEqual(out["window_name"], "desktop")
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["center"], "#808040")


class ColormapTests(unittest.TestCase):
    def test_colormap_follows_window_name(self):
        cmap = colour_map([(0, 0x1234, 0x5678, 0x9abc), (1, 0xffff, 0xffff, 0xffff)])
        data = make_header(1, 1, 8, visual=3, name="xterm", ncolors=2) + cmap + b"\x01"
        self.assertEqual(parse(data)["window_name"], "xterm")
        self.assertEqual(parse(data)["points"]["top_left"], "#ffffff")

    def test_colormap_without_window_name(self):
        cmap = colour_map([(0, 0x0700, 0xefef, 0x3333)])
        data = make_header(1, 1, 8, visual=3, ncolors=1) + cmap + bytes(1)
        # 16-bit components: the high byte is kept.
        self.assertEqual(parse(data)["points"]["top_left"], "#07ef33")

    def test_missing_entry_is_black(self):
        cmap = colour_map([(0, 0xffff, 0xffff, 0xffff)])
        data = make_header(1, 1, 8, visual=3, ncolors=1) + cmap + b"\x05"
        self.assertEqual(parse(data)["points"]["top_left"], "#000000")

    def test_entry_selected_by_pixel_value(self):
        cmap = colour_map([(3, 0xff00, 0, 0), (9, 0, 0xff00, 0)])
        data = make_header(1, 1, 8, visual=3, ncolors=2) + cmap + b"\x09"
        self.assertEqual(parse(data)["points"]["top_left"], "#00ff00")


class LayoutTests(unittest.TestCase):
    def test_rows_use_bytes_per_line(self):
        # 3 pixels per row, padded to 4 bytes: only a bytes_per_line stride keeps
        # the rows aligned.
        cmap = colour_map([(v, v << 8, v << 8, v << 8) for v in range(10)])
        pixels = bytes([1, 2, 3, 0xFF, 4, 5, 6, 0xFF])
        data = make_header(3, 2, 8, bpl=4, ncolors=10) + cmap + pixels
        out = parse(data)
        self.assertEqual(out["points"]["top_right"], "#030303")
        self.assertEqual(out["points"]["bottom_right"], "#060606")

    def test_little_endian_32bpp(self):
        masks = (0x00FF0000, 0x0000FF00, 0x000000FF)
        pixel = bytes.fromhex("0000ff00")          # 0x00ff0000 as stored LSB first
        data = make_header(1, 1, 32, visual=4, masks=masks, byte_order=0) + pixel
        out = parse(data)
        self.assertEqual(out["byte_order"], "lsb")
        self.assertEqual(out["points"]["top_left"], "#ff0000")

    def test_little_endian_16bpp(self):
        masks = (0xF800, 0x07E0, 0x001F)
        data = make_header(1, 1, 16, visual=4, masks=masks, byte_order=0) + bytes.fromhex("ffff")
        self.assertEqual(parse(data)["points"]["top_left"], "#ffffff")

    def test_little_endian_header(self):
        data = make_header(2, 1, 8, order="<") + b"\x00\x01"
        out = parse(data)
        self.assertEqual((out["width"], out["height"], out["image_bytes"]), (2, 1, 2))

    def test_short_bytes_per_line_rejected(self):
        with self.assertRaises(XwdError):
            parse(make_header(4, 1, 8, bpl=2) + bytes(2))

    def test_true_color_is_widened(self):
        masks = (0xF800, 0x07E0, 0x001F)
        white = struct.pack(">HH", 0xFFFF, 0xFFFF)[:2]
        data = make_header(1, 1, 16, visual=4, masks=masks) + white
        self.assertEqual(parse(data)["points"]["top_left"], "#ffffff")

    def test_true_color_component_rounding(self):
        masks = (0xF800, 0x07E0, 0x001F)
        # green 0x3f of 0x3f -> 255, red 0x10 of 0x1f -> round(16*255/31) = 132
        pixel = struct.pack(">H", 0x8000 | 0x07E0)
        data = make_header(1, 1, 16, visual=4, masks=masks) + pixel
        self.assertEqual(parse(data)["points"]["top_left"], "#84ff00")

    def test_low_bpp_bit_order(self):
        cmap = colour_map([(0, 0, 0, 0), (1, 0xff00, 0xff00, 0xff00)])
        data = make_header(2, 1, 1, ncolors=2, bpl=1, bit_order=0) + cmap + b"\x01"
        out = parse(data)
        self.assertEqual(out["points"]["top_left"], "#ffffff")
        self.assertEqual(out["points"]["top_right"], "#000000")


class CompletenessTests(unittest.TestCase):
    def test_truncated_pixels(self):
        data = make_header(4, 2, 8) + bytes(4)
        out = parse(data)
        self.assertFalse(out["complete"])
        self.assertIsNone(out["points"])

    def test_padding_bytes_do_not_affect_size(self):
        data = make_header(3, 2, 8, bpl=8) + bytes(16)
        out = parse(data)
        self.assertEqual(out["image_bytes"], 16)
        self.assertTrue(out["complete"])
