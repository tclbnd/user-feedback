import unittest

from w64kit.w64 import summary

from files import DATA, chunk, fmt, pcm, w64


class BasicTests(unittest.TestCase):
    def test_header_fields(self):
        out = summary(w64(fmt(1, 44100, 16), chunk(DATA, pcm([(100,)] * 10))))
        self.assertEqual((out["channels"], out["sample_rate"], out["bits"]), (1, 44100, 16))
        self.assertEqual(out["chunks"][0], "fmt")


if __name__ == "__main__":
    unittest.main()
