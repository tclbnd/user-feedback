import unittest

from imxkit.imx import HIST, NPIX, W, ImxError, parse
from imxkit.imx import Imx


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/film.imx", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (320, 160, 3))
        self.assertTrue(all(f["state"] == "decoded" for f in out["frames"]))
        self.assertEqual([f["history"] for f in out["frames"]], [17692, 17792, 17802])

    def test_bad_magic(self):
        with self.assertRaises(ImxError):
            parse(bytes(16))


class DecoderTests(unittest.TestCase):
    def test_fill_count_is_exactly_n(self):
        v = Imx()
        v.frame(bytes([0x82, 0x07]))  # fill, low six bits = 2, value 7
        self.assertEqual(list(v.pix[:3]), [7, 7, 0])
        self.assertEqual(v.pos, 2)

    def test_skips_land_updates_at_right_offsets(self):
        v = Imx()
        v.frame(bytes([
            0x05,                 # short skip of 5
            0x81, 0x09,           # fill 1 pixel with 9
            0xC0, 0x0A,           # long skip of 10
            0x41, 0x03,           # literal pixel 3
        ]))
        self.assertEqual(v.pix[4], 0)
        self.assertEqual(v.pix[5], 9)
        self.assertEqual(v.pix[6:15], bytes(9))
        self.assertEqual(v.pix[16], 3)
        # two drawn pixels recorded, skips recorded nothing
        self.assertEqual(v.pos, 2)
        self.assertEqual(list(v.hist[:2]), [9, 3])

    def test_literals_are_recorded_in_history(self):
        v = Imx()
        v.frame(bytes([0x43, 0x0A, 0x0B, 0x0C]))  # three literals
        self.assertEqual(list(v.pix[:3]), [10, 11, 12])
        self.assertEqual(list(v.hist[:3]), [10, 11, 12])
        self.assertEqual(v.pos, 3)

    def test_history_copy_reads_ring_and_appends_again(self):
        v = Imx()
        v.frame(bytes([0x42, 0x11, 0x22]))       # literals -> hist 0,1
        v.frame(bytes([
            0x02,                                  # skip 2
            0x40, 0x00, 0x00, 0x02,                # history copy: off 0, count 2
        ]))
        self.assertEqual(list(v.pix[2:4]), [0x11, 0x22])
        # copied bytes are appended behind the two literals
        self.assertEqual(list(v.hist[:4]), [0x11, 0x22, 0x11, 0x22])
        self.assertEqual(v.pos, 4)

    def test_history_copy_wraps_ring_offset(self):
        v = Imx()
        v.frame(bytes([0x41, 0x5A]))               # one literal at slot 0
        v.pos = HIST - 1                            # force cursor to last slot
        v.frame(bytes([
            0x01,                                   # draw cursor starts at 0: skip 1
            0x40, 0x00, 0x00, 0x01,                 # history copy: off 0, count 1
        ]))
        self.assertEqual(v.pix[1], 0x5A)
        self.assertEqual(v.hist[HIST - 1], 0x5A)
        self.assertEqual(v.pos, 0)

    def test_state_persists_between_packets(self):
        v = Imx()
        v.frame(bytes([0x41, 0x04]))                # draw one pixel
        v.frame(bytes([0x02, 0x81, 0x08]))          # skip 2, then fill with 8
        self.assertEqual(v.pix[0], 4)               # untouched pixel survived
        self.assertEqual(v.pix[2], 8)
        self.assertEqual(v.pos, 2)

    def test_decode_stops_at_frame_end(self):
        v = Imx()
        v.frame(bytes([0xFF, 0xFF]))                # long skip beyond the frame
        self.assertEqual(v.pos, 0)
        self.assertFalse(any(v.pix))
        self.assertLessEqual(len(v.pix), NPIX)


if __name__ == "__main__":
    unittest.main()
