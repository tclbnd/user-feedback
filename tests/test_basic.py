import unittest

from dmckit.dmc import DmcError, parse


class BasicTests(unittest.TestCase):
    def test_flags(self):
        data = b"NESDMC\x00" + bytes([0xCF, 0, 0, 0]) + bytes(0x8000)
        out = parse(data)
        self.assertTrue(out["loop"])
        self.assertTrue(out["irq"])

    def test_not_a_snapshot(self):
        with self.assertRaises(DmcError):
            parse(b"NESXXX" + bytes(20))
