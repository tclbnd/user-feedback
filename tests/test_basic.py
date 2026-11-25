import unittest

from ymkit.ym import parse

from dumps import dump


class BasicTests(unittest.TestCase):
    def test_strings(self):
        out = parse(dump())
        self.assertEqual((out["format"], out["title"], out["author"], out["comment"]), ("YM6!", "Test", "Me", None))

    def test_end_marker(self):
        self.assertTrue(parse(dump())["end_marker"])
