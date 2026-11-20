import unittest

from sbckit.stream import summary

from captures import frame


class BasicTests(unittest.TestCase):
    def test_mono_stream(self):
        data = frame(rate=2, blocks=16, mode=0, subbands=8, bitpool=32) * 10
        out = summary(data)
        self.assertEqual((out["sample_rate"], out["channel_mode"], out["subbands"]), (44100, "mono", 8))
        self.assertEqual((out["frame_length"], out["frames"]), (72, 10))
        self.assertEqual(out["duration"], "64/2205")


if __name__ == "__main__":
    unittest.main()
