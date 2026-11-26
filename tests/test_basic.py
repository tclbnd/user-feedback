import struct
import unittest

from lynxspr.suzy import SuzyError, parse


class BasicTests(unittest.TestCase):
    def test_empty_chain(self):
        data = b"LYNXSCB\0" + struct.pack("<H", 0) + bytes(32) + bytes(0x10000)
        out = parse(data)
        self.assertEqual(out["sprites_drawn"], 0)
        self.assertEqual(out["pen_pixels"][0], 160 * 102)

    def test_not_a_snapshot(self):
        with self.assertRaises(SuzyError):
            parse(b"LYNXSCX\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
