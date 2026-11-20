import unittest

from aifftool.info import summary

from files import comm, form, pcm, ssnd


class BasicTests(unittest.TestCase):
    def test_mono_16(self):
        frames = [(0,), (8192,), (-16384,), (100,)]
        out = summary(form([comm(1, 4, 16, 8000), ssnd(pcm(frames, 16))]))
        self.assertEqual(out["format"], "AIFF")
        self.assertEqual((out["channels"], out["bits"], out["frames"]), (1, 16, 4))
        self.assertEqual(out["frames_in_file"], 4)
        self.assertEqual(out["peak"], [0.5])


if __name__ == "__main__":
    unittest.main()
