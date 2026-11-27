import unittest

from ga20kit.voices import Ga20Error, parse


class BasicTests(unittest.TestCase):
    def test_all_voices_off(self):
        out = parse(b"GA20SNAP" + bytes(32) + bytes(256))
        self.assertEqual(out["voices_on"], 0)
        self.assertEqual(len(out["voices"]), 4)

    def test_not_a_snapshot(self):
        with self.assertRaises(Ga20Error):
            parse(b"GA20SNAX" + bytes(64))


if __name__ == "__main__":
    unittest.main()
