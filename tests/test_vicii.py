import unittest

from sprkit.sprites import parse, SpriteError, W, H


def make(regs, ptrs=b"", mem=b""):
    r = bytearray(47); r[0x21] = 6
    for i, v in regs.items():
        r[i] = v
    p = bytearray(8)
    for i, v in (ptrs or {}).items():
        p[i] = v
    return b"C64SPR" + bytes(r) + bytes(p) + bytes(mem)


def block(rows):
    out = bytearray()
    for triple in rows:
        out += bytes(triple)
    return out


FULL = (0xFF, 0xFF, 0xFF)


class PositionTests(unittest.TestCase):
    def test_origin_offset(self):
        # Sprite register X=24, Y=50 lands on window pixel (0, 0).
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        out = parse(make({0x15: 0b1, 0x00: 24, 0x01: 50}, {0: 0}, mem))
        s = out["sprites"][0]
        self.assertEqual((s["x"], s["y"]), (0, 0))
        self.assertEqual((s["width"], s["height"]), (24, 21))
        self.assertEqual(s["pixels"], 24 * 21)
        self.assertEqual(out["histogram"][6], 320 * 200 - 24 * 21)

    def test_negative_position(self):
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        out = parse(make({0x15: 0b1, 0x00: 0, 0x01: 0}, {0: 0}, mem))
        s = out["sprites"][0]
        self.assertEqual((s["x"], s["y"]), (-24, -50))
        # Only the bottom-right 0x0... corner reaches the window.
        self.assertEqual(out["histogram"][6], 320 * 200)

    def test_x_msb_ninth_bit(self):
        # With the MSB set, X=256+24=280 -> window x=256; the 24 columns
        # 256..279 still lie inside the 320 px window, so all of it shows.
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        out = parse(make({0x15: 0b1, 0x10: 0b1, 0x00: 24, 0x01: 50}, {0: 0}, mem))
        s = out["sprites"][0]
        self.assertEqual(s["x"], 256)
        self.assertEqual(out["histogram"][6], 320 * 200 - 24 * 21)

    def test_x_msb_fully_offscreen(self):
        # X=256+120=376 -> window x=352: entirely past the right edge.
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        out = parse(make({0x15: 0b1, 0x10: 0b1, 0x00: 120, 0x01: 50}, {0: 0}, mem))
        self.assertEqual(out["sprites"][0]["x"], 352)
        self.assertEqual(out["histogram"][6], 320 * 200)

    def test_x_msb_partial_clip(self):
        # X=256+80=336 -> window x=312: 8 of 24 columns visible.
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        out = parse(make({0x15: 0b1, 0x10: 0b1, 0x00: 80, 0x01: 50}, {0: 0}, mem))
        s = out["sprites"][0]
        self.assertEqual(s["x"], 312)
        self.assertEqual(s["pixels"], 24 * 21)
        self.assertEqual(out["histogram"][6], 320 * 200 - 8 * 21)


class MulticolorTests(unittest.TestCase):
    def test_pair_colours(self):
        # One row holds the sequence 01,10,11,00 three times (12 pairs).
        # Each non-transparent pair paints two physical pixels.
        row = (0b01101100, 0b01101100, 0b01101100)
        b = block([row] + [(0, 0, 0)] * 20)
        mem = bytearray(16384); mem[0:63] = b
        regs = {0x15: 0b1, 0x1C: 0b1, 0x00: 24, 0x01: 50,
                0x25: 10, 0x26: 7, 0x27: 2}
        out = parse(make(regs, {0: 0}, mem))
        s = out["sprites"][0]
        self.assertEqual(s["pixels"], 9 * 2)  # 9 non-transparent pairs
        h = out["histogram"]
        self.assertEqual(h[10], 3 * 2)  # pair 01 -> multicolour 0
        self.assertEqual(h[2], 3 * 2)   # pair 10 -> own colour
        self.assertEqual(h[7], 3 * 2)   # pair 11 -> multicolour 1
        self.assertEqual(h[6], 320 * 200 - 18)

    def test_multicolor_pixel_count(self):
        # All four pair values repeated -> 18 non-transparent physical px/row.
        row = (0b01101100,) * 3
        b = block([row] * 21)
        mem = bytearray(16384); mem[0:63] = b
        regs = {0x15: 0b1, 0x1C: 0b1, 0x00: 24, 0x01: 50,
                0x25: 1, 0x26: 2, 0x27: 3}
        out = parse(make(regs, {0: 0}, mem))
        self.assertEqual(out["sprites"][0]["pixels"], 18 * 21)


class ExpansionTests(unittest.TestCase):
    def test_x_expansion(self):
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        out = parse(make({0x15: 0b1, 0x1D: 0b1, 0x00: 24, 0x01: 50}, {0: 0}, mem))
        s = out["sprites"][0]
        self.assertEqual((s["width"], s["height"]), (48, 21))
        self.assertEqual(s["pixels"], 48 * 21)

    def test_y_expansion(self):
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        out = parse(make({0x15: 0b1, 0x17: 0b1, 0x00: 24, 0x01: 50}, {0: 0}, mem))
        s = out["sprites"][0]
        self.assertEqual((s["width"], s["height"]), (24, 42))
        self.assertEqual(s["pixels"], 24 * 42)

    def test_both_expansion(self):
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        # Note: $D001 holds sprite 0's Y, while $D017 is the Y-expand latch.
        out = parse(make({0x15: 0b1, 0x17: 0b1, 0x1D: 0b1, 0x00: 24, 0x01: 50}, {0: 0}, mem))
        s = out["sprites"][0]
        self.assertEqual((s["width"], s["height"]), (48, 42))
        self.assertEqual(s["pixels"], 48 * 42)
        # Window y=0, so the full 42-row height is inside the 200-row window.
        self.assertEqual(out["histogram"][6], 320 * 200 - 48 * 42)

    def test_both_expansion_bottom_clip(self):
        # Register Y=250 -> window y=200: entirely below the window.
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        out = parse(make({0x15: 0b1, 0x17: 0b1, 0x1D: 0b1, 0x00: 24, 0x01: 250}, {0: 0}, mem))
        self.assertEqual(out["sprites"][0]["y"], 200)
        self.assertEqual(out["histogram"][6], 320 * 200)

    def test_multicolor_x_expansion_width(self):
        # Multicolour + X expansion stays 48 px wide, pairs duplicated.
        row = (0b01101100,) * 3
        b = block([row] * 21)
        mem = bytearray(16384); mem[0:63] = b
        regs = {0x15: 0b1, 0x1C: 0b1, 0x1D: 0b1, 0x00: 24, 0x01: 50,
                0x25: 1, 0x26: 2, 0x27: 3}
        out = parse(make(regs, {0: 0}, mem))
        s = out["sprites"][0]
        self.assertEqual(s["width"], 48)
        self.assertEqual(s["pixels"], 18 * 21 * 2)


class PriorityTests(unittest.TestCase):
    def test_lower_number_covers_higher(self):
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b; mem[64:127] = b
        regs = {0x15: 0b11, 0x00: 24, 0x01: 50, 0x02: 24, 0x03: 50,
                0x27: 5, 0x28: 9}
        out = parse(make(regs, {0: 0, 1: 1}, mem))
        # Sprite 0 wins the whole overlap.
        self.assertEqual(out["histogram"][5], 24 * 21)
        self.assertEqual(out["histogram"][9], 0)


class TruncationTests(unittest.TestCase):
    def test_missing_block_pixels_null(self):
        mem = bytearray(16384); mem[0:63] = block([FULL] * 21)
        full = make({0x15: 0b11, 0x00: 24, 0x01: 50, 0x02: 24, 0x03: 50}, {0: 0, 1: 200}, mem)
        cut = full[:6 + 47 + 8 + 200 * 64]
        out = parse(cut)
        self.assertFalse(out["complete"])
        by_n = {s["n"]: s for s in out["sprites"]}
        self.assertIsNotNone(by_n[0]["pixels"])
        self.assertIsNone(by_n[1]["pixels"])
        # The missing sprite is simply not drawn.
        self.assertEqual(out["histogram"][6], 320 * 200 - 24 * 21)


class ColourTests(unittest.TestCase):
    def test_register_nibble_masking(self):
        # Only the low 4 bits of colour registers are used on the VIC-II.
        b = block([FULL] * 21)
        mem = bytearray(16384); mem[0:63] = b
        out = parse(make({0x15: 0b1, 0x00: 24, 0x01: 50,
                          0x21: 0xF6, 0x27: 0xAD}, {0: 0}, mem))
        self.assertEqual(out["background"], 6)
        self.assertEqual(out["histogram"][0xD], 24 * 21)  # 0xAD -> colour 13

    def test_multicolor_shared_register_masking(self):
        row = (0b01110000, 0, 0)  # pairs: 01, 11, then transparent
        b = block([row] + [(0, 0, 0)] * 20)
        mem = bytearray(16384); mem[0:63] = b
        regs = {0x15: 0b1, 0x1C: 0b1, 0x00: 24, 0x01: 50,
                0x25: 0x1A, 0x26: 0x2B, 0x27: 0x3C}
        out = parse(make(regs, {0: 0}, mem))
        h = out["histogram"]
        self.assertEqual(h[0xA], 2)  # mc0 0x1A -> 10
        self.assertEqual(h[0xB], 2)  # mc1 0x2B -> 11


class OrderingTests(unittest.TestCase):
    def test_enabled_sprites_in_numeric_order(self):
        b = block([FULL] * 21)
        mem = bytearray(16384)
        for p in range(3):
            mem[p * 64:p * 64 + 63] = b
        regs = {0x15: 0b10100101, 0x00: 24, 0x01: 50, 0x04: 24, 0x05: 50,
                0x06: 24, 0x07: 50}
        out = parse(make(regs, {0: 0, 2: 1, 5: 2, 7: 3}, mem))
        self.assertEqual([s["n"] for s in out["sprites"]], [0, 2, 5, 7])


if __name__ == "__main__":
    unittest.main()
