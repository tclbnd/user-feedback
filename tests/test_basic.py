import unittest

from fdskit.regs import FdsError, parse


class BasicTests(unittest.TestCase):
    def test_silent_wave(self):
        data = b"FDSAUDIO" + bytes(11) + bytes(2) + bytes(64) + bytes(32)
        out = parse(data)
        self.assertEqual(out["wave_sum"], 0)
        self.assertEqual(out["level"], "0")

    def test_not_a_snapshot(self):
        with self.assertRaises(FdsError):
            parse(b"FDSAUDIX" + bytes(120))


if __name__ == "__main__":
    unittest.main()
