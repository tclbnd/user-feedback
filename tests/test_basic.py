import unittest

from c93kit.c93 import W, H, C93Error, Decoder, parse


def pack(frames):
    return b"C93VIDEO" + len(frames).to_bytes(2, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


def packet(flag, blocks, palette=b""):
    """Build a packet from 960 (type, payload) pairs, type bytes low nibble first."""
    out = bytearray([flag])
    for i in range(0, len(blocks), 2):
        (t0, p0), (t1, p1) = blocks[i], blocks[i + 1]
        out.append(t0 | t1 << 4)
        out += p0 + p1
    return bytes(out) + palette


def decode(frames):
    """Decode packets, returning (picture, palette) for every frame."""
    dec = Decoder()
    out = []
    for body in frames:
        _, i = dec.frame(body)
        out.append((bytes(dec.pics[i]), list(dec.pals[i])))
    return out


def solid_palette():
    return bytes([0, 0, 0, 255, 0, 0]) + bytes(762)


def blank(count=960, size=64):
    return [(0x0F, bytes(size))] * count


class RowMaskTests(unittest.TestCase):
    def one_block(self, mask):
        body = packet(0x03, [(0x08, bytes([0, 255]) + bytes([mask] * 8))] + blank(959), solid_palette())
        return decode([body])[0][0]

    def test_bit_zero_is_left_pixel(self):
        self.assertEqual(self.one_block(0x01)[0:8], bytes([255, 0, 0, 0, 0, 0, 0, 0]))

    def test_bit_seven_is_right_pixel(self):
        self.assertEqual(self.one_block(0x80)[0:8], bytes([0, 0, 0, 0, 0, 0, 0, 255]))

    def test_each_row_uses_its_own_mask(self):
        pic = self.one_block(0x0F)
        for y in range(8):
            self.assertEqual(pic[y * W:y * W + 8], bytes([255] * 4 + [0] * 4))


class TypeByteTests(unittest.TestCase):
    def test_low_nibble_is_the_first_block(self):
        raw = bytes([7] * 64)
        body = packet(0x03, [(0x0F, raw), (0x08, bytes([0, 255]) + bytes(8))] + blank(958), solid_palette())
        pic = decode([body])[0][0]
        self.assertEqual(pic[0:8], bytes([7] * 8))
        self.assertEqual(pic[8:16], bytes(8))


class PaletteTests(unittest.TestCase):
    def test_frame_without_palette_keeps_previous(self):
        key = packet(0x03, [(0x0F, bytes([5] * 64))] + blank(959), solid_palette())
        copy = packet(0x00, [(0x0E, b"")] * 960)
        (pic0, pal0), (pic1, pal1) = decode([key, copy])
        self.assertEqual(pal1, pal0)
        self.assertEqual(pic1, pic0)

    def test_frame_without_palette_is_not_black(self):
        key = packet(0x03, [(0x0F, bytes([5] * 64))] + blank(959), solid_palette())
        nxt = packet(0x00, [(0x0F, bytes([5] * 64))] + blank(959))
        out = parse(pack([key, nxt]))
        self.assertEqual(out["frames"][0]["crc32"], out["frames"][1]["crc32"])


class QuadrantCopyTests(unittest.TestCase):
    def key_frame(self):
        blocks = []
        for y in range(0, H, 8):
            for x in range(0, W, 8):
                blocks.append((0x0F, bytes((x + 8 * y + i) & 0xFF for i in range(64))))
        return packet(0x02, blocks)

    def test_quadrants_are_left_to_right_then_top_to_bottom(self):
        # Source quadrants: (0,0) top-left, (8,0) top-right,
        # (0,8) bottom-left, (8,8) bottom-right of the previous picture.
        offs = [0 * W + 0, 0 * W + 8, 8 * W + 0, 8 * W + 8]
        body = packet(0x02, [(0x06, b"".join(o.to_bytes(2, "little") for o in offs))]
                             + [(0x0E, b"")] * 959)
        (old, _), (new, _) = decode([self.key_frame(), body])
        for j, off in enumerate(offs):
            dx, dy = (j % 2) * 4, (j // 2) * 4
            fx, fy = off % W, off // W
            for i in range(4):
                want = old[(fy + i) * W + fx:(fy + i) * W + fx + 4]
                got = new[(dy + i) * W + dx:(dy + i) * W + dx + 4]
                self.assertEqual(got, want, "quadrant %d row %d" % (j, i))


class BasicTests(unittest.TestCase):
    def test_intra_frame(self):
        body = packet(0x02, [(0x0F, bytes(64))] * 960)
        out = parse(pack([body]))
        self.assertEqual(out["frames"][0]["ok"], True)
        self.assertEqual(out["frames"][0]["key"], True)

    def test_bad_magic(self):
        with self.assertRaises(C93Error):
            parse(b"C93AUDIO" + bytes(4))


if __name__ == "__main__":
    unittest.main()
