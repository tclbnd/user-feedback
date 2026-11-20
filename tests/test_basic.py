import unittest

from caftool.info import summary

from files import caf, data, desc, info, pcm


class BasicTests(unittest.TestCase):
    def test_stereo_peak_and_info(self):
        audio = pcm([(8192, -16384), (100, 200)])
        out = summary(caf(desc(48000.0), info([("artist", "Field Unit 3")]), data(audio)))
        self.assertEqual((out["format_id"], out["channels"], out["bits"]), ("lpcm", 2, 16))
        self.assertEqual(out["peak"], [0.25, 0.5])
        self.assertEqual(out["info"], {"artist": "Field Unit 3"})


if __name__ == "__main__":
    unittest.main()
