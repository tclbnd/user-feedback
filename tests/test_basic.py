import unittest

from scdpcm.chip import SnapshotError, parse


class BasicTests(unittest.TestCase):
    def test_not_snapshot(self):
        with self.assertRaises(SnapshotError):
            parse(b"POKEYREG" + bytes(80))

    def test_truncated(self):
        data = b"SCDPCM\0\0" + bytes([0xFF]) + bytes(56) + bytes(1000)
        out = parse(data)
        self.assertFalse(out["complete"])
        self.assertIsNone(out["channels"][0]["length"])
