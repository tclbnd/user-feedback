import unittest

from apekit.ape import parse

from tags import audio, item, tag


class BasicTests(unittest.TestCase):
    def test_title_only(self):
        out = parse(audio(500) + tag([item("Title", "Harbour")], header=False))
        self.assertEqual(out["version"], "2.000")
        self.assertEqual(out["items"][0]["key"], "Title")


if __name__ == "__main__":
    unittest.main()
