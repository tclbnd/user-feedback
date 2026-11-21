import unittest

from modkit.info import summary

from mods import module, sample


class BasicTests(unittest.TestCase):
    def test_title_and_samples(self):
        out = summary(module("Night Drive", [sample("kick", 0), sample("pad", 0)]))
        self.assertEqual((out["format"], out["signature"], out["title"]), ("31-sample", "M.K.", "Night Drive"))
        self.assertEqual(out["song_length"], 1)
        self.assertEqual(out["patterns"], 1)


if __name__ == "__main__":
    unittest.main()
