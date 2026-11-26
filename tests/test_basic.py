import unittest

from wsvdp.screen import WsError, parse


class BasicTests(unittest.TestCase):
    def test_layers_off(self):
        out = parse(b"WSVDP\0\0\0" + bytes(64) + bytes(2) + bytes(0x10000))
        self.assertEqual(out["mode"], "mono")
        self.assertEqual(out["sprite_pixels"], 0)
        self.assertEqual(len(out["first_row"]), 1)

    def test_not_a_snapshot(self):
        with self.assertRaises(WsError):
            parse(b"WSVDX\0\0\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
