import unittest

from y4mlab import psnr
from y4mlab.probe import probe


class BasicTests(unittest.TestCase):
    def test_small_420(self):
        frame = bytes([100] * 16) + bytes([128] * 8)
        data = b"YUV4MPEG2 W4 H4 F25:1 Ip A1:1 C420jpeg\n" + (b"FRAME\n" + frame) * 3
        out = probe(data)
        self.assertEqual((out["frames"], out["frame_size"], out["fps"]), (3, 24, "25/1"))
        self.assertEqual(out["mean_luma"], [100.0, 100.0, 100.0])

    def test_psnr(self):
        self.assertEqual(psnr.psnr([10, 20], [10, 22]), 45.121)


if __name__ == "__main__":
    unittest.main()
