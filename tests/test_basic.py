import unittest

from vickit.screen import SnapshotError, parse


def snap(code_row):
    r = [0] * 16
    r[2], r[3], r[15] = 1, 1 << 1, 0x18
    return b"VIC20SNP" + bytes(r) + bytes([0]) + bytes([2]) + bytes([code_row] * 8) + bytes(255 * 8)


class BasicTests(unittest.TestCase):
    def test_single_cell(self):
        out = parse(snap(0xF0))
        self.assertEqual(out["first_line"], "22221111")

    def test_not_a_snapshot(self):
        with self.assertRaises(SnapshotError):
            parse(b"VIC20XXX" + bytes(30))
