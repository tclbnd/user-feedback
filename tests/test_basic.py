import unittest

from k051649.wave import SnapshotError, parse


class BasicTests(unittest.TestCase):
    def test_not_snapshot(self):
        with self.assertRaises(SnapshotError):
            parse(b"SAA1099\0" + bytes(40))

    def test_truncated(self):
        out = parse(b"K051649\0" + bytes([0]) + bytes(64))
        self.assertFalse(out["complete"])
