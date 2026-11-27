import unittest

from dockit.osc import DocError, parse


class BasicTests(unittest.TestCase):
    def test_reset_state(self):
        out = parse(b"ES5503DC" + bytes(0xE3) + bytes(0x20000))
        self.assertEqual(out["oscillators"], 1)
        self.assertEqual(out["oscs"][0]["stops_at"], 0)

    def test_not_a_snapshot(self):
        with self.assertRaises(DocError):
            parse(b"ES5503DX" + bytes(300))


if __name__ == "__main__":
    unittest.main()
