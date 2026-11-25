import unittest

from sprkit.sprites import SpriteError, parse


def snapshot(bg):
    regs = bytearray(47)
    regs[0x21] = bg
    return b"C64SPR" + bytes(regs) + bytes(8) + bytes(16384)


class BasicTests(unittest.TestCase):
    def test_empty_window(self):
        out = parse(snapshot(6))
        self.assertEqual(out["histogram"][6], 320 * 200)
        self.assertEqual(out["sprites"], [])

    def test_not_a_snapshot(self):
        with self.assertRaises(SpriteError):
            parse(b"C64XXX" + bytes(100))
