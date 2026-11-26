import unittest

from pcvid.video import VideoError, _cga_colours, _ega_colour, parse


def _dump(mode, select, palette, memory):
    regs = (list(palette) + [0] * 16)[:16]
    return b"PCVID" + bytes([mode, select]) + bytes(regs) + bytes(memory)


class BasicTests(unittest.TestCase):
    def test_blank_cga(self):
        data = b"PCVID" + bytes([4, 0]) + bytes(16) + bytes(0x4000)
        out = parse(data)
        self.assertEqual(out["colors"], {"#000000": 320 * 200})

    def test_not_a_dump(self):
        with self.assertRaises(VideoError):
            parse(b"PCXXX" + bytes(30))

    def test_unsupported_mode(self):
        with self.assertRaises(VideoError):
            parse(_dump(0x13, 0, [], bytes(0x4000)))

    def test_incomplete(self):
        out = parse(_dump(0x0D, 0, [], bytes(100)))
        self.assertFalse(out["complete"])
        self.assertIsNone(out["colors"])
        self.assertIsNone(out["first_line"])

    def test_cga_colour_select(self):
        # 3D9h = 0x31: background from the low nibble, intensity bit, palette 1.
        self.assertEqual(_cga_colours(0x31), ["#0000aa", "#55ffff", "#ff55ff", "#ffffff"])
        # Low intensity palette 0 on black.
        self.assertEqual(_cga_colours(0x00), ["#000000", "#00aa00", "#aa0000", "#aa5500"])
        # The colour select register picks both palette and background.
        self.assertNotEqual(_cga_colours(0x00), _cga_colours(0x20))
        self.assertNotEqual(_cga_colours(0x00), _cga_colours(0x01))

    def test_ega_palette_register(self):
        self.assertEqual(_ega_colour(0x00), "#000000")
        self.assertEqual(_ega_colour(0x01), "#0000aa")
        self.assertEqual(_ega_colour(0x04), "#aa0000")
        self.assertEqual(_ega_colour(0x14), "#aa5500")  # brown: red + high green bit
        self.assertEqual(_ega_colour(0x3F), "#ffffff")


class LayoutTests(unittest.TestCase):
    def test_cga_rows_are_interleaved(self):
        # Even rows live in the first 8 KB, odd rows in the second; fill them differently.
        mem = bytearray(0x4000)
        mem[:8000] = b"\x55" * 8000   # even scan lines, pixel value 1
        mem[0x2000:0x2000 + 8000] = b"\xaa" * 8000  # odd scan lines, pixel value 2
        out = parse(_dump(4, 0x30, [], mem))
        self.assertEqual(out["first_line"], [["#55ffff", 320]])
        self.assertEqual(out["last_line"], [["#ff55ff", 320]])
        self.assertEqual(out["colors"], {"#55ffff": 32000, "#ff55ff": 32000})

    def test_cga_background_and_palette_follow_select(self):
        mem = bytearray(0x4000)  # pixel value 0 everywhere
        out = parse(_dump(4, 0x2E, [], mem))  # palette 1, high intensity, yellow background
        self.assertEqual(out["colors"], {"#ffff55": 320 * 200})

    def test_cga640_background_from_select(self):
        mem = bytearray(0x4000)
        out = parse(_dump(6, 0x03, [], mem))  # cyan background, no foreground pixels
        self.assertEqual(out["width"], 640)
        self.assertEqual(out["colors"], {"#00aaaa": 640 * 200})

    def test_ega_planes(self):
        # Four bit planes, plane 0 first: plane 0 selects colour 1, plane 3 colour 8.
        mem = bytearray(4 * 8000)
        mem[0] = 0xF0                 # plane 0, pixels 0-3 set -> attribute 1
        mem[3 * 8000] = 0x0F          # plane 3, pixels 4-7 set -> attribute 8
        palette = [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x14, 0x07,
                   0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F]
        out = parse(_dump(0x0D, 0, palette, mem))
        self.assertEqual(out["first_line"], [["#0000aa", 4], ["#555555", 4], ["#000000", 312]])
        self.assertEqual(out["last_line"], [["#000000", 320]])
        self.assertEqual(out["colors"], {"#000000": 63992, "#0000aa": 4, "#555555": 4})

    def test_hercules_banks(self):
        mem = bytearray(0x8000)
        for y in range(348):
            off = (y & 3) * 0x2000 + (y >> 2) * 90
            mem[off:off + 90] = (b"\xff" if y % 2 else b"\x00") * 90
        out = parse(_dump(0x80, 0, [], mem))
        self.assertEqual(out["width"], 720)
        self.assertEqual(out["height"], 348)
        self.assertEqual(out["first_line"], [["#000000", 720]])
        self.assertEqual(out["last_line"], [["#ffffff", 720]])
        self.assertEqual(out["colors"], {"#000000": 174 * 720, "#ffffff": 174 * 720})
