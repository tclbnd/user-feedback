import unittest

from bubkit.voices import BubError, parse


class BasicTests(unittest.TestCase):
    def test_two_channels(self):
        out = parse(b"K005289S" + bytes(11) + bytes(512))
        self.assertEqual(len(out["channels"]), 2)
        self.assertTrue(out["prom_complete"])

    def test_not_a_snapshot(self):
        with self.assertRaises(BubError):
            parse(b"K005289X" + bytes(600))


if __name__ == "__main__":
    unittest.main()
