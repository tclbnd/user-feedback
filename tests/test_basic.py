import unittest

from shrkit.shr import ShrError, parse


class BasicTests(unittest.TestCase):
    def test_blank(self):
        out = parse(bytes(32768))
        self.assertEqual((out["modes"], out["colors"]), ({"320": 200, "640": 0}, 1))

    def test_wrong_size(self):
        with self.assertRaises(ShrError):
            parse(bytes(32000))
