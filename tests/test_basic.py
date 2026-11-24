import unittest

from gbskit.gbs import parse

from rips import rip


class BasicTests(unittest.TestCase):
    def test_title(self):
        out = parse(rip())
        self.assertEqual((out["title"], out["songs"], out["data_bytes"]), ("Test", 3, 64))

    def test_version(self):
        self.assertEqual(parse(rip())["version"], 1)
