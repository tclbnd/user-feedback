import unittest

from adpcmkit.info import summary

from clips import ima_block, ima_extra, wav


class BasicTests(unittest.TestCase):
    def test_mono_ima_fields(self):
        data = wav(0x11, 1, 8000, 36, ima_block([(1234, 10)], bytes(32)) * 2, ima_extra(65))
        out = summary(data)
        self.assertEqual((out["format"], out["channels"], out["sample_rate"], out["blocks"]), ("ima_adpcm", 1, 8000, 2))
        self.assertEqual(out["head"][0][0], 1234)


if __name__ == "__main__":
    unittest.main()
