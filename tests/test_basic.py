import unittest

from adxkit.adx import AdxError, parse


class BasicTests(unittest.TestCase):
    def test_magic(self):
        with self.assertRaises(AdxError):
            parse(b"RIFF" + bytes(60))

    def test_fields(self):
        head = bytearray(0x40)
        head[0:2] = b"\x80\x00"
        head[2:4] = (0x3C).to_bytes(2, "big")
        head[4:8] = bytes([3, 18, 4, 1])
        head[0x3A:0x40] = b"(c)CRI"
        out = parse(bytes(head))
        self.assertEqual((out["encoding"], out["block_size"], out["channels"]), ("adx_standard", 18, 1))
