import unittest

from pvgfx.screen import PvError, parse


def snap(reg7=0, vram=None, pram=None, prom=b""):
    return (b"PV1000VR" + bytes([reg7])
            + (vram if vram is not None else bytes(1024))
            + (pram if pram is not None else bytes(1024))
            + prom)


def pattern(p0=0, p1=0, p2=0):
    # 32 bytes per pattern: 8 unused bytes, then the three 8-byte planes
    return bytes(8) + bytes([p0] * 8) + bytes([p1] * 8) + bytes([p2] * 8)


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        out = parse(b"PV1000VR" + b"\x00" + bytes(2048) + bytes(8 * 256 * 32))
        self.assertEqual(out["first_row"], [["#000000", 256]])
        self.assertTrue(out["display"])

    def test_not_a_snapshot(self):
        with self.assertRaises(PvError):
            parse(b"PV1000XX" + bytes(3000))

    def test_border_columns(self):
        # border colour 2 (green); every tile solid colour 7 (white)
        out = parse(snap(reg7=2, prom=pattern(0xFF, 0xFF, 0xFF) * 256))
        self.assertEqual(out["border"], "#00ff00")
        self.assertEqual(out["first_row"],
                         [["#00ff00", 8], ["#ffffff", 240], ["#00ff00", 8]])
        self.assertEqual(out["last_row"],
                         [["#00ff00", 8], ["#ffffff", 240], ["#00ff00", 8]])

    def test_colour_channels(self):
        # first plane set -> high colour bit -> blue; last plane -> red
        prom = bytearray(32 * 256)
        prom[0:32] = pattern(p0=0xFF)
        out = parse(snap(prom=bytes(prom)))
        self.assertEqual(out["first_row"][1], ["#0000ff", 240])
        prom[0:32] = pattern(p2=0xFF)
        out = parse(snap(prom=bytes(prom)))
        self.assertEqual(out["first_row"][1], ["#ff0000", 240])

    def test_pattern_ram_tiles(self):
        vram = bytearray(1024)
        vram[1] = 0xE0  # first pattern RAM tile, row 0 column 1
        vram[2] = 0xFF  # last pattern RAM tile
        pram = bytearray(1024)
        pram[0:32] = pattern(p0=0xFF, p1=0xFF)   # colour 6 -> cyan
        pram[31 * 32:32 * 32] = pattern(p1=0xFF)  # colour 2 -> green
        out = parse(snap(vram=bytes(vram), pram=bytes(pram)))
        self.assertEqual(out["ram_cells"], 2)
        self.assertEqual(out["first_row"][1], ["#00ffff", 8])
        self.assertEqual(out["first_row"][2], ["#00ff00", 8])

    def test_pattern_bank(self):
        prom = bytearray(32 * 512)
        prom[32 * 256:32 * 257] = pattern(p2=0xFF)  # bank 1 pattern 0 -> red
        out = parse(snap(reg7=1 << 5, prom=bytes(prom)))
        self.assertEqual(out["pattern_bank"], 1)
        self.assertEqual(out["first_row"][1], ["#ff0000", 240])
        out = parse(snap(reg7=0, prom=bytes(prom)))
        self.assertEqual(out["first_row"], [["#000000", 256]])

    def test_display_off(self):
        vram = bytearray([0xE0] * 1024)
        out = parse(snap(reg7=0x08 | 5, vram=bytes(vram),
                         pram=pattern(0xFF, 0xFF, 0xFF) * 32))
        self.assertFalse(out["display"])
        self.assertEqual(out["ram_cells"], 0)
        self.assertEqual(out["first_row"], [["#ff00ff", 256]])
        self.assertEqual(out["last_row"], [["#ff00ff", 256]])

    def test_missing_patterns(self):
        # no pattern ROM at all: every drawn cell is missing, shown as colour 0
        out = parse(snap())
        self.assertEqual(out["missing_patterns"], 24 * 30)
        self.assertEqual(out["first_row"], [["#000000", 256]])


if __name__ == "__main__":
    unittest.main()
