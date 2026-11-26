import unittest

from gbapack.unpack import PackError, parse


class BasicTests(unittest.TestCase):
    def test_literals(self):
        data = bytes([0x10, 4, 0, 0, 0x00]) + b"ABCD"
        out = parse(data)
        self.assertEqual(out["type"], "lz77")
        self.assertEqual(out["crc32"], "db1720a5")

    def test_unknown_type(self):
        with self.assertRaises(PackError):
            parse(bytes([0x50, 0, 0, 0]))
