import unittest

from x1kit.chans import X1Error, parse


class BasicTests(unittest.TestCase):
    def test_nothing_keyed(self):
        out = parse(b"SETAX1SN" + bytes(0x2000) + bytes(0x1000))
        self.assertEqual(out["channels_on"], 0)
        self.assertEqual(out["output_rate"], 31250)

    def test_not_a_snapshot(self):
        with self.assertRaises(X1Error):
            parse(b"SETAX1SX" + bytes(100))


if __name__ == "__main__":
    unittest.main()
