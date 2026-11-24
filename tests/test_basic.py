import unittest

from itkit.it import parse

from mods import minimal


class BasicTests(unittest.TestCase):
    def test_title(self):
        out = parse(minimal())
        self.assertEqual((out["title"], out["speed"], out["tempo"]), ("Test", 6, 125))

    def test_empty_lists(self):
        out = parse(minimal())
        self.assertEqual((out["samples"], out["pattern_rows"]), ([], []))
