import struct
import unittest
import zlib

from pixkit.pix import FORMATS, STD_PAL, PixError, parse

MAGIC = bytes([0, 0, 0, 0x12, 0, 0, 0, 0x08, 0, 0, 0, 0x02, 0, 0, 0, 0x02])


def widen5(v):
    return (v << 3) | (v >> 2)


def widen6(v):
    return (v << 2) | (v >> 4)


def header_chunk(fmt, w, h, name):
    body = bytes([fmt, 0, 0]) + struct.pack(">HH", w, h) + b"\0\0" + name.encode() + b"\0"
    return struct.pack(">II", 3, len(body)) + body


def data_chunk(payload):
    # A type-0x21 chunk: size excludes the 8-byte on-disk sub-header.
    return struct.pack(">II", 0x21, len(payload)) + b"\0" * 8 + payload


def build(fmt, w, h, name, payload, palette=None):
    out = MAGIC + header_chunk(fmt, w, h, name)
    if palette is not None:
        out += header_chunk(7, 1, 256, "pal")
        out += data_chunk(b"".join(struct.pack(">I", c) for c in palette))
    out += data_chunk(payload)
    return out


class BasicTests(unittest.TestCase):
    def test_bad_magic(self):
        with self.assertRaises(PixError):
            parse(bytes(32))

    def test_format_list(self):
        self.assertIn(3, FORMATS)
        self.assertEqual(FORMATS[4][0], "rgb565")
        self.assertEqual(FORMATS[5][0], "rgb555")
        self.assertIn(18, FORMATS)

    def test_long_name_rgb565(self):
        # Names longer than the old fixed skip must not desync the parser.
        name = "a_really_long_texture_name_0123456789"
        p1 = (31 << 11) | (0 << 5) | 0
        p2 = (0 << 11) | (63 << 5) | 0
        data = build(4, 2, 1, name, struct.pack(">HH", p1, p2))
        r = parse(data)
        exp = bytes((widen5(31), widen6(0), widen5(0),
                     widen5(0), widen6(63), widen5(0)))
        self.assertEqual(r["format"], "rgb565")
        self.assertEqual((r["width"], r["height"]), (2, 1))
        self.assertEqual(r["crc32"], "%08x" % zlib.crc32(exp))
        self.assertIsNone(r["alpha_crc32"])

    def test_rgb555_channels(self):
        v = (10 << 10) | (20 << 5) | 31
        data = build(5, 1, 1, "s555", struct.pack(">H", v))
        r = parse(data)
        exp = bytes((widen5(10), widen5(20), widen5(31)))
        self.assertEqual(r["format"], "rgb555")
        self.assertEqual(r["crc32"], "%08x" % zlib.crc32(exp))

    def test_xrgb_channels(self):
        payload = bytes([10, 20, 30, 0, 40, 50, 60, 255])
        r = parse(build(7, 2, 1, "x", payload))
        self.assertEqual(r["crc32"], "%08x" % zlib.crc32(bytes([10, 20, 30, 40, 50, 60])))
        self.assertIsNone(r["alpha_crc32"])

    def test_argb_channels_and_alpha(self):
        payload = bytes([10, 20, 30, 200, 40, 50, 60, 100])
        r = parse(build(8, 2, 1, "a", payload))
        self.assertEqual(r["crc32"], "%08x" % zlib.crc32(bytes([10, 20, 30, 40, 50, 60])))
        self.assertEqual(r["alpha_crc32"], "%08x" % zlib.crc32(bytes([200, 100])))

    def test_ya8(self):
        r = parse(build(18, 2, 1, "y", bytes([10, 200, 30, 40])))
        self.assertEqual(r["crc32"], "%08x" % zlib.crc32(bytes([10, 10, 10, 30, 30, 30])))
        self.assertEqual(r["alpha_crc32"], "%08x" % zlib.crc32(bytes([200, 40])))

    def test_embedded_palette(self):
        palette = [i * 0x010101 for i in range(256)]
        indices = bytes([0, 1, 2, 255])
        r = parse(build(3, 4, 1, "withpal", indices, palette=palette))
        exp = bytearray()
        for i in indices:
            v = palette[i]
            exp += bytes((v >> 16, v >> 8 & 255, v & 255))
        self.assertEqual(r["format"], "pal8")
        self.assertEqual(r["crc32"], "%08x" % zlib.crc32(bytes(exp)))

    def test_embedded_palette_with_payload_padding(self):
        # Mirrors sign.pix: the palette chunk carries 8 extra bytes after
        # the 256 entries, so framing must follow the chunk size.
        palette = [(i * 0x00010101) & 0xFFFFFF for i in range(256)]
        pal_payload = b"".join(struct.pack(">I", c) for c in palette) + b"\0" * 8
        indices = bytes(range(8))
        data = (MAGIC + header_chunk(3, 8, 1, "sign") + header_chunk(7, 1, 256, "pal")
                + data_chunk(pal_payload) + data_chunk(indices))
        r = parse(data)
        exp = bytearray()
        for i in indices:
            v = palette[i]
            exp += bytes((v >> 16, v >> 8 & 255, v & 255))
        self.assertEqual(r["crc32"], "%08x" % zlib.crc32(bytes(exp)))

    def test_standard_palette_fallback(self):
        indices = bytes([0, 1, 2, 64])
        r = parse(build(3, 4, 1, "nopal", indices))
        exp = bytearray()
        for i in indices:
            v = STD_PAL[i]
            exp += bytes((v >> 16, v >> 8 & 255, v & 255))
        self.assertEqual(r["crc32"], "%08x" % zlib.crc32(bytes(exp)))

    def test_truncated_raises(self):
        with self.assertRaises(PixError):
            parse(b"")
        with self.assertRaises(PixError):
            parse(MAGIC + struct.pack(">II", 3, 4) + b"\x06\0\0")


if __name__ == "__main__":
    unittest.main()
