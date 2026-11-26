import unittest

from opllkit.regs import SnapshotError, parse


class BasicTests(unittest.TestCase):
    def test_not_snapshot(self):
        with self.assertRaises(SnapshotError):
            parse(b"N163RAM\0" + bytes(128))

    def test_truncated(self):
        out = parse(b"YM2413\0\0" + bytes(20))
        self.assertFalse(out["complete"])
