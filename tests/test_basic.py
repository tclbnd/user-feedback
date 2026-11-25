import unittest

from webpkit.webp import parse

from files import still


class BasicTests(unittest.TestCase):
    def test_still(self):
        out = parse(still(64, 48))
        self.assertEqual((out["image"]["codec"], out["image"]["width"], out["image"]["height"]), ("lossy", 64, 48))

    def test_not_animated(self):
        out = parse(still())
        self.assertEqual((out["animated"], out["frames"], out["canvas"]), (False, [], None))
