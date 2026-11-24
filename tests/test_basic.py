import unittest

from rstmkit.rstm import RstmError, parse


class BasicTests(unittest.TestCase):
    def test_magic(self):
        with self.assertRaises(RstmError):
            parse(b"RIFF" + bytes(60))

    def test_bom(self):
        with self.assertRaises(RstmError):
            parse(b"RSTM\x00\x00" + bytes(60))
