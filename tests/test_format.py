import unittest
import zlib

from degaskit.degas import DegasError, parse, ste


def packbits(data):
    """Degas Elite PackBits encoder (run length 257 - n)."""
    out = bytearray()
    i = 0
    while i < len(data):
        run = 1
        while i + run < len(data) and run < 128 and data[i + run] == data[i]:
            run += 1
        if run >= 2:
            out.append(257 - run)
            out.append(data[i])
            i += run
            continue
        start = i
        i += 1
        while i < len(data) and i - start < 128:
            nxt = 1
            while i + nxt < len(data) and nxt < 2 and data[i + nxt] == data[i]:
                nxt += 1
            if nxt >= 2:
                break
            i += 1
        out.append(i - start - 1)
        out += data[start:i]
    return bytes(out)


def pack_rows(rows, width, planes):
    """Pixel indices (rows of width) -> planar scanline bytes."""
    body = bytearray()
    bpl = width // 8
    for row in rows:
        for p in range(planes):
            line = bytearray(bpl)
            for x in range(bpl * 8):
                if x < width and (row[x] >> p) & 1:
                    line[x >> 3] |= 0x80 >> (x & 7)
            body += line
    return bytes(body)


def build(res, rows, palette, compressed=False, anim=b""):
    width = {0: 320, 1: 640, 2: 640}[res]
    planes = {0: 4, 1: 2, 2: 1}[res]
    body = pack_rows(rows, width, planes)
    if compressed:
        body = packbits(body)
    word = res | (0x8000 if compressed else 0)
    head = bytes([word >> 8, word & 0xFF])
    palette = list(palette) + [0] * (16 - len(palette))
    pal = b"".join(bytes([v >> 8, v & 0xFF]) for v in palette)
    return head + pal + body + anim


class PaletteTests(unittest.TestCase):
    def test_ste_expands_each_channel_to_full_range(self):
        self.assertEqual(ste(0xFFF), (255, 255, 255))
        self.assertEqual(ste(0xF00), (255, 0, 0))
        self.assertEqual(ste(0x0F0), (0, 255, 0))
        self.assertEqual(ste(0x00F), (0, 0, 255))
        self.assertEqual(ste(0x800), (136, 0, 0))
        self.assertEqual(ste(0x000), (0, 0, 0))

    def test_palette_reports_ste_words(self):
        pal = [0x000, 0xFFF, 0xF00, 0x0F0]
        out = parse(build(1, [[0] * 640] * 200, pal))
        self.assertEqual(out["palette"], ["000", "fff", "f00", "0f0"])

    def test_low_resolution_palette_has_sixteen_entries(self):
        out = parse(build(0, [[0] * 320] * 200, [k * 0x111 for k in range(16)]))
        self.assertEqual(len(out["palette"]), 16)
        self.assertEqual(out["palette"][15], "fff")


class ResolutionTests(unittest.TestCase):
    def test_medium_resolution_is_two_planes(self):
        out = parse(build(1, [[0] * 640] * 200, [0, 0x7F0, 0x08F, 0xFFF]))
        self.assertEqual((out["width"], out["height"], out["planes"]), (640, 200, 2))
        self.assertEqual(len(out["palette"]), 4)

    def test_medium_resolution_pixels(self):
        rows = [[0] * 640 for _ in range(200)]
        rows[1] = [1] * 640
        rows[2] = [2] * 640
        rows[3] = [3] * 640
        out = parse(build(1, rows, [0x000, 0xFFF, 0xF00, 0x00F]))
        # Row 0 black, row 1 white, row 2 red, row 3 blue.
        stride = 3 * 640
        full = bytes([0, 0, 0]) * 640 + bytes([255, 255, 255]) * 640 + \
               bytes([255, 0, 0]) * 640 + bytes([0, 0, 255]) * 640
        self.assertEqual(out["first_row"], "%08x" % zlib.crc32(full[:stride]))

    def test_high_resolution_is_monochrome(self):
        rows = [[0] * 640 for _ in range(400)]
        out = parse(build(2, rows, [0] * 16))
        self.assertEqual((out["width"], out["height"], out["planes"]), (640, 400, 1))
        self.assertIsNone(out["palette"])

    def test_monochrome_white_where_bit_is_zero(self):
        rows = [[0] * 640 for _ in range(400)]
        rows[-1] = [1] * 640
        out = parse(build(2, rows, [0] * 16))
        self.assertEqual(out["first_row"], "%08x" % zlib.crc32(bytes([255, 255, 255]) * 640))
        self.assertEqual(out["last_row"], "%08x" % zlib.crc32(bytes(3 * 640)))


class CompressionTests(unittest.TestCase):
    def test_repeat_run_is_257_minus_count(self):
        # 0x81 means a run of 257 - 0x81 = 128 bytes.  250 of them fill all
        # 32000 bytes of a low-resolution picture exactly; with the plain
        # PackBits count (256 - n = 127) the data would fall one byte short.
        stream = bytes([0x81, 0x00]) * 250
        data = bytes([0x80, 0x00]) + bytes(32) + stream
        out = parse(data)
        self.assertTrue(out["compressed"])
        self.assertEqual(out["crc32"], "%08x" % zlib.crc32(bytes(3 * 320 * 200)))

    def test_compressed_matches_uncompressed(self):
        rows = [[(x // 8 + y // 8) % 16 for x in range(320)] for y in range(200)]
        plain = parse(build(0, rows, [k * 0x111 for k in range(16)]))
        packed = parse(build(0, rows, [k * 0x111 for k in range(16)], compressed=True))
        self.assertEqual(packed["crc32"], plain["crc32"])
        self.assertEqual(packed["first_row"], plain["first_row"])
        self.assertEqual(packed["last_row"], plain["last_row"])

    def test_long_repeat_crosses_scanlines(self):
        rows = [[0] * 320 for _ in range(200)]
        rows[100] = [5] * 320
        plain = parse(build(0, rows, [k * 0x111 for k in range(16)]))
        packed = parse(build(0, rows, [k * 0x111 for k in range(16)], compressed=True))
        self.assertEqual(packed["crc32"], plain["crc32"])

    def test_truncated_compressed_data(self):
        # A repeat run that needs a byte past the end of the file.
        with self.assertRaises(DegasError):
            parse(bytes([0x80, 0x00]) + bytes(32) + b"\xff\x00" + b"\x81")

    def test_compressed_data_ends_early(self):
        # Compression that stops well before the picture is complete.
        with self.assertRaises(DegasError):
            parse(bytes([0x80, 0x00]) + bytes(32) + bytes([0x00, 0x00]))
    def test_animation_table(self):
        anim = b"".join(v.to_bytes(2, "big") for v in
                        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16])
        out = parse(build(0, [[0] * 320] * 200, [0] * 16, compressed=True, anim=anim))
        self.assertEqual(out["animation"][0],
                         {"first": 1, "last": 5, "direction": 9, "delay": 13})
        self.assertEqual(out["animation"][3],
                         {"first": 4, "last": 8, "direction": 12, "delay": 16})

    def test_animation_absent_when_uncompressed(self):
        out = parse(build(0, [[0] * 320] * 200, [0] * 16))
        self.assertIsNone(out["animation"])


class ExampleTests(unittest.TestCase):
    """The bundled pictures must decode exactly as the ST displays them."""

    def _parse(self, name):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "examples", name)
        with open(path, "rb") as fh:
            return parse(fh.read())

    def test_compressed_title(self):
        out = self._parse("title.pc1")
        self.assertTrue(out["compressed"])
        self.assertEqual((out["width"], out["height"], out["planes"]), (320, 200, 4))
        self.assertEqual(out["palette"][15], "fff")
        self.assertEqual(out["palette"][12], "0ff")
        self.assertEqual(out["crc32"], "84896145")
        self.assertEqual(out["animation"][0],
                         {"first": 8, "last": 15, "direction": 1, "delay": 6})
        self.assertEqual(out["animation"][1],
                         {"first": 0, "last": 0, "direction": 0, "delay": 0})

    def test_medium_resolution_sheet(self):
        out = self._parse("sheet.pi2")
        self.assertFalse(out["compressed"])
        self.assertEqual((out["width"], out["height"], out["planes"]), (640, 200, 2))
        self.assertEqual(out["palette"], ["000", "7f0", "08f", "fff"])
        self.assertEqual(out["crc32"], "6c3676ca")
        self.assertIsNone(out["animation"])


if __name__ == "__main__":
    unittest.main()
