import unittest

from ifokit.ifo import parse

from discs import ifo, pgc


class BasicTests(unittest.TestCase):
    def test_standard(self):
        out = parse(ifo([pgc((0, 0, 5, 0), [(0, 0, 5, 0)], [1])], [[(1, 1)]]))
        self.assertEqual((out["standard"], out["aspect"]), ("PAL", "4:3"))

    def test_structure(self):
        out = parse(ifo([pgc((0, 0, 9, 0), [(0, 0, 4, 0), (0, 0, 5, 0)], [1, 2])], [[(1, 1), (1, 2)]]))
        self.assertEqual((out["pgcs"][0]["programs"], out["pgcs"][0]["cells"], out["titles"][0]["chapters"]), (2, 2, 2))
