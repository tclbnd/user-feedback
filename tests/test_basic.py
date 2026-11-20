import unittest

from ivfscan import bdrate, obu


class BasicTests(unittest.TestCase):
    def test_small_obus(self):
        tu = bytes([0x12, 0x00]) + bytes([0x32, 0x03, 1, 2, 3])
        self.assertEqual([(o.type, o.payload) for o in obu.obus(tu)], [(2, b""), (6, b"\x01\x02\x03")])

    def test_gain(self):
        self.assertEqual(bdrate.average_gain([(1, 30.0), (2, 33.0)], [(1, 30.5), (2, 33.1), (3, 36)]), 0.3)


if __name__ == "__main__":
    unittest.main()
