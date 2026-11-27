import unittest

from snkkit.wave import SnkError, parse


class BasicTests(unittest.TestCase):
    def test_one_voice(self):
        out = parse(b"SNKWAVE\0" + bytes([1]) + bytes(6))
        self.assertEqual(len(out["voices"]), 1)
        self.assertEqual(len(out["voices"][0]["wave"]), 16)

    def test_not_a_snapshot(self):
        with self.assertRaises(SnkError):
            parse(b"SNKWAVX\0" + bytes(7))


if __name__ == "__main__":
    unittest.main()
