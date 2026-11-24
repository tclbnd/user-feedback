import struct
import unittest

from neokit.neo import NeoError, parse

BLANK = bytes(128 + 32000)

PLANES = {0: 4, 1: 2, 2: 1}
GROUPS = {0: 20, 1: 40, 2: 40}  # 16-pixel groups per scanline


def make(res=0, pal=(), limits=0, speed=0, steps=0, pixels=()):
    """Build a .neo file; pixels are (x, y, colour index) tuples."""
    head = bytearray(128)
    struct.pack_into(">H", head, 2, res)
    for i, v in enumerate(pal):
        struct.pack_into(">H", head, 4 + 2 * i, v)
    struct.pack_into(">HHH", head, 48, limits, speed, steps)
    screen = bytearray(32000)
    planes = PLANES.get(res, 1)
    for x, y, idx in pixels:
        off = (y * GROUPS.get(res, 20) + x // 16) * planes * 2
        for pl in range(planes):
            if idx >> pl & 1:
                woff = off + 2 * pl
                w, = struct.unpack_from(">H", screen, woff)
                struct.pack_into(">H", screen, woff, w | 1 << (15 - x % 16))
    return bytes(head + screen)


class BasicTests(unittest.TestCase):
    def test_resolution(self):
        out = parse(BLANK)
        self.assertEqual((out["resolution"], out["width"], out["filename"]), ("low", 320, None))

    def test_offset(self):
        self.assertEqual(parse(BLANK)["offset"], [0, 0])

    def test_medium(self):
        out = parse(make(res=1))
        self.assertEqual((out["resolution"], out["width"], out["height"]), ("medium", 640, 200))
        self.assertEqual(len(out["palette"]), 4)

    def test_high(self):
        out = parse(make(res=2, pixels=[(639, 399, 1)]))
        self.assertEqual((out["resolution"], out["width"], out["height"]), ("high", 640, 400))
        self.assertEqual(len(out["palette"]), 2)
        self.assertEqual(out["points"]["top_left"], {"index": 0, "color": "#ffffff"})
        self.assertEqual(out["points"]["bottom_right"], {"index": 1, "color": "#000000"})

    def test_bad_resolution(self):
        self.assertRaises(NeoError, parse, make(res=3))

    def test_interleaved_msb_left(self):
        out = parse(make(pixels=[(0, 0, 1), (15, 0, 2), (16, 0, 3)]))
        pts = out["points"]
        self.assertEqual(pts["top_left"]["index"], 1)
        self.assertEqual(pts["x15"]["index"], 2)
        self.assertEqual(pts["x16"]["index"], 3)

    def test_ste_palette(self):
        out = parse(make(pal=[0x0000, 0x0FFF, 0x0777, 0x0888, 0x08F8]))
        self.assertEqual(out["palette"][:5], ["#000000", "#ffffff", "#eeeeee", "#111111", "#11ff11"])

    def test_cycling_off(self):
        self.assertIsNone(parse(BLANK)["cycling"])

    def test_cycling_on(self):
        out = parse(make(limits=0x804B, speed=0x8008, steps=6))
        self.assertEqual(out["cycling"],
                         {"left": 4, "right": 11, "direction": "right", "delay": 8, "steps": 6})
