import unittest

from vbvip.world import WorldError, parse


class BasicTests(unittest.TestCase):
    def test_not_snapshot(self):
        with self.assertRaises(WorldError):
            parse(b"GBCBG\0" + bytes(40))

    def test_truncated(self):
        data = b"VBVIP\0" + bytes(8) + bytes(12) + bytes(100)
        self.assertFalse(parse(data)["complete"])
