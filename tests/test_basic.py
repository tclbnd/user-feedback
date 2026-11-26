import struct
import unittest

from pokeykit.regs import DumpError, parse


class BasicTests(unittest.TestCase):
    def test_not_dump(self):
        with self.assertRaises(DumpError):
            parse(b"SAP\r\n" + bytes(20))

    def test_silent_frames(self):
        data = b"POKEYREG" + bytes(2) + struct.pack("<I", 3) + bytes(27)
        out = parse(data)
        self.assertEqual(out["frames"], 3)
        self.assertEqual(out["channels"][0]["sounding_frames"], 0)
