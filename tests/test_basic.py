import unittest

from rlekit.msrle import Bytes, RleError, parse, rle4, rle8


def pack(w, h, depth, frames):
    return b"MSRLEPAK" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([depth, 0]) + len(frames).to_bytes(2, "little") \
        + bytes(1024) + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_single_run(self):
        out = parse(pack(4, 1, 8, [bytes([4, 7, 0, 1])]))
        self.assertEqual(out["frames"][0]["state"], "decoded")

    def test_bad_depth(self):
        with self.assertRaises(RleError):
            parse(pack(4, 1, 16, []))


class SpecTests(unittest.TestCase):
    def test_rle8_bottom_up_and_odd_literal_pad_and_delta(self):
        # absolute 3 (odd -> one pad byte), delta up 1, run of one 9,
        # end of line, absolute 4 (even -> no pad), end of bitmap
        s = bytes([0, 3, 1, 2, 3, 0, 0, 2, 0, 1, 1, 9, 0, 0, 0, 4, 4, 5, 6, 7, 0, 1])
        pic = bytearray(12)
        self.assertEqual(rle8(pic, 4, 3, Bytes(s)), "decoded")
        rows = [pic[i * 4:(i + 1) * 4] for i in range(3)]
        self.assertEqual(rows[0], bytearray([4, 5, 6, 7]))  # top row coded last
        self.assertEqual(rows[1], bytearray([0, 0, 0, 9]))  # delta kept x=3
        self.assertEqual(rows[2], bytearray([1, 2, 3, 0]))  # bottom row coded first

    def test_rle8_delta_is_dx_then_dy(self):
        pic = bytearray(12)
        self.assertEqual(rle8(pic, 4, 3, Bytes(bytes([0, 2, 2, 2, 1, 8, 0, 1]))), "decoded")
        self.assertEqual(pic[2], 8)  # top row, x=2
        self.assertEqual(pic[1], 0)

    def test_rle4_nibble_order(self):
        # run of 3 with byte 0xAB -> A,B,A; absolute 3 -> 1,2,3; end of line;
        # absolute 4 -> 5,6,7,8; end of bitmap
        s = bytes([3, 0xAB, 0, 3, 0x12, 0x30, 0, 0, 0, 4, 0x56, 0x78, 0, 1])
        pic = bytearray(16)
        self.assertEqual(rle4(pic, 8, 2, Bytes(s)), "decoded")
        self.assertEqual(pic[0:8], bytearray([5, 6, 7, 8, 0, 0, 0, 0]))
        self.assertEqual(pic[8:16], bytearray([0xA, 0xB, 0xA, 1, 2, 3, 0, 0]))

    def test_rle4_odd_literal_byte_count_is_padded(self):
        # absolute 5 pixels -> 3 data bytes -> one pad byte, then a run of 2
        s = bytes([0, 5, 0x12, 0x34, 0x50, 0, 2, 0x9A, 0, 1])
        pic = bytearray(8)
        self.assertEqual(rle4(pic, 8, 1, Bytes(s)), "decoded")
        self.assertEqual(list(pic), [1, 2, 3, 4, 5, 9, 10, 0])

    def test_rle4_delta_is_dx_then_dy(self):
        pic = bytearray(8)
        self.assertEqual(rle4(pic, 4, 2, Bytes(bytes([0, 2, 1, 1, 1, 0xC0, 0, 1]))), "decoded")
        self.assertEqual(pic[1], 0xC)  # top row, x=1

    def test_frames_share_one_picture(self):
        # frame 1 paints both rows with 7; frame 2 only repaints the top row,
        # so the bottom row of frame 1 must survive into frame 2
        import zlib
        pal = bytearray(1024)
        pal[4 * 7 + 2] = 7  # palette entries are stored B, G, R
        pal[4 * 8 + 2] = 8
        head = b"MSRLEPAK" + (2).to_bytes(2, "little") + (2).to_bytes(2, "little") \
            + bytes([8, 0]) + (2).to_bytes(2, "little") + bytes(pal)
        frames = [bytes([2, 7, 0, 0, 2, 7, 0, 1]), bytes([0, 2, 0, 1, 2, 8, 0, 1])]
        data = head + b"".join(len(f).to_bytes(4, "little") + f for f in frames)
        out = parse(data)
        self.assertEqual([f["state"] for f in out["frames"]], ["decoded", "decoded"])
        rgb1 = bytes([7, 0, 0]) * 4
        rgb2 = bytes([8, 0, 0, 8, 0, 0, 7, 0, 0, 7, 0, 0])  # rows top to bottom
        self.assertEqual(out["frames"][0]["crc32"], "%08x" % zlib.crc32(rgb1))
        self.assertEqual(out["frames"][1]["crc32"], "%08x" % zlib.crc32(rgb2))


if __name__ == "__main__":
    unittest.main()
