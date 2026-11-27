import unittest

from qtrlekit.qtrle import decode


def packet(payload, flags=0, palette=False):
    body = bytes([0, flags | (8 if palette else 0)]) + payload
    if palette:
        body += b"\x00" * 8
    return len(body).to_bytes(4, "big") + body


def picture(w, h):
    return [0] * (w * h)


class RowSkipTests(unittest.TestCase):
    def test_row_skip_descriptor_lands_on_middle_rows(self):
        # 0x82 skips two whole rows, so the run must appear on row 2, not row 0.
        pkt = packet(bytes([0x82, 0x00, 0xFE, 9, 9, 9, 9, 0xFF]))
        pic = picture(8, 4)
        state = decode(pic, 8, 4, 8, pkt)
        self.assertEqual(state, "decoded")
        self.assertEqual(pic[0:8], [0] * 8)
        self.assertEqual(pic[8:16], [0] * 8)
        self.assertEqual(pic[16:24], [9] * 8)
        self.assertEqual(pic[24:32], [0] * 8)

    def test_row_start_descriptor_is_group_multiplied_once(self):
        # A within-row skip of one 4-pixel group starts at column 4.
        pkt = packet(bytes([0x01, 0xFE, 5, 5, 5, 5, 0xFF]))
        pic = picture(16, 1)
        decode(pic, 16, 1, 8, pkt)
        self.assertEqual(pic, [0, 0, 0, 0] + [5] * 8 + [0] * 4)


class Depth8Tests(unittest.TestCase):
    def test_literal_writes_every_pixel_not_one_in_four(self):
        # A positive opcode of 4 writes four literal pixels.
        pkt = packet(bytes([0x00, 0x04, 1, 2, 3, 4, 0xFF]))
        pic = picture(8, 1)
        decode(pic, 8, 1, 8, pkt)
        self.assertEqual(pic, [1, 2, 3, 4, 0, 0, 0, 0])

    def test_skip_opcode_advances_without_drawing(self):
        # At 8 bpp a skip count of 1 moves one whole 4-pixel group, so the
        # literal lands on column 4 and columns 0-3 stay untouched.
        pkt = packet(bytes([0x00, 0x00, 1, 0x01, 8, 0xFF]))
        pic = picture(8, 1)
        decode(pic, 8, 1, 8, pkt)
        self.assertEqual(pic, [0, 0, 0, 0, 8, 0, 0, 0])


class PackedPixelTests(unittest.TestCase):
    def test_4bpp_nibbles_are_msb_first(self):
        # One literal byte 0x12 yields two pixels: high nibble 1 then low 2.
        pkt = packet(bytes([0x00, 0x01, 0x12, 0xFF]))
        pic = picture(8, 1)
        decode(pic, 8, 1, 4, pkt)
        self.assertEqual(pic, [1, 2, 0, 0, 0, 0, 0, 0])

    def test_2bpp_pairs_are_msb_first(self):
        # One literal byte 0xE4 unpacks to 3,2,1,0 from the top of the byte.
        pkt = packet(bytes([0x00, 0x01, 0xE4, 0xFF]))
        pic = picture(16, 1)
        decode(pic, 16, 1, 2, pkt)
        self.assertEqual(pic, [3, 2, 1, 0] + [0] * 12)

    def test_1bpp_bits_are_msb_first(self):
        # 0x80 marks a new row at row 0; the run repeats one pattern byte
        # (0xF0 -> 1,1,1,1,0,0,0,0) twice to fill 16 pixels.
        pkt = packet(bytes([0x80, 0x00, 0xFE, 0xF0, 0xFF]))
        pic = picture(16, 2)
        state = decode(pic, 16, 2, 1, pkt)
        self.assertEqual(state, "decoded")
        self.assertEqual(pic[0:16], [1, 1, 1, 1, 0, 0, 0, 0] * 2)
        self.assertEqual(pic[16:32], [0] * 16)


class Depth16Tests(unittest.TestCase):
    def test_colour_is_big_endian_rgb555(self):
        # Literal 0x001f then run 0x7fff, matching QuickTime's byte order.
        pkt = packet(bytes([0x00, 0x01, 0x00, 0x1F, 0xFE, 0x7F, 0xFF, 0xFF]))
        pic = picture(4, 1)
        decode(pic, 4, 1, 16, pkt)
        self.assertEqual(pic, [0x001F, 0x7FFF, 0x7FFF, 0])


class StateTests(unittest.TestCase):
    def test_empty_packet_leaves_picture_unchanged(self):
        pkt = (4).to_bytes(4, "big")
        pic = [3] * 6
        self.assertEqual(decode(pic, 3, 2, 16, pkt), "unchanged")
        self.assertEqual(pic, [3] * 6)

    def test_truncated_packet_stops_but_keeps_drawn_pixels(self):
        # A completed run is kept; the literal that follows runs out of bytes.
        pkt = packet(bytes([0x00, 0xFE, 0x12, 0x34, 0x02, 0x00, 0x11]))
        pic = picture(4, 1)
        self.assertEqual(decode(pic, 4, 1, 16, pkt), "stopped")
        self.assertEqual(pic[0:2], [0x1234, 0x1234])
        self.assertEqual(pic[2:], [0, 0])

    def test_frames_accumulate_on_one_picture(self):
        first = packet(bytes([0x00, 0x01, 0x00, 0x1F, 0xFF]))
        second = packet(bytes([0x81, 0x00, 0x01, 0x00, 0x2A, 0xFF]))
        pic = picture(2, 2)
        self.assertEqual(decode(pic, 2, 2, 16, first), "decoded")
        self.assertEqual(decode(pic, 2, 2, 16, second), "decoded")
        self.assertEqual(pic, [0x001F, 0, 0x002A, 0])


if __name__ == "__main__":
    unittest.main()
