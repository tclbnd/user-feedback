import unittest

from m2vkit.stream import summary

from streams import mpeg2, seq, seq_ext


class BasicTests(unittest.TestCase):
    def test_pal_progressive(self):
        data = mpeg2(seq(720, 576, 2, 3, 3750) + seq_ext(0x48, 1, 1), [("I", {})] + [("P", {})] * 11, gop_every=12)
        out = summary(data)
        self.assertEqual((out["width"], out["height"]), (720, 576))
        self.assertEqual(out["frame_rate"], "25/1")
        self.assertEqual(out["bit_rate"], 1500000)
        self.assertEqual(out["pictures"]["I"], 1)
        self.assertEqual(out["pictures"]["P"], 11)
        self.assertEqual(out["frames"], 12)
        self.assertEqual(out["duration"], "12/25")
        self.assertEqual(out["profile"], "Main")
        self.assertEqual(out["level"], "Main")


if __name__ == "__main__":
    unittest.main()
