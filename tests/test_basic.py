import unittest

from vdckit.screen import VdcError, parse


def snap(row):
    r = [0] * 37
    r[1], r[6], r[9], r[26] = 1, 1, 7, 0xF0
    return b"VDCSNAP\x00" + bytes(r) + bytes([0]) + bytes([0]) + bytes([row] * 8 + [0] * 8) + bytes(511 * 16)


class BasicTests(unittest.TestCase):
    def test_single_cell(self):
        self.assertEqual(parse(snap(0xF0))["first_line"], "ffff0000")

    def test_not_a_snapshot(self):
        with self.assertRaises(VdcError):
            parse(b"VDCSNAPX" + bytes(40))
