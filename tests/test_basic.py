import unittest

from k7232kit.pcm import K7232Error, parse


class BasicTests(unittest.TestCase):
    def test_nothing_keyed(self):
        out = parse(b"K007232S" + bytes(14) + b"\x00" + bytes(64))
        self.assertFalse(out["channels"][0]["keyed"])
        self.assertEqual(out["rom_size"], 64)

    def test_not_a_snapshot(self):
        with self.assertRaises(K7232Error):
            parse(b"K007232X" + bytes(40))


if __name__ == "__main__":
    unittest.main()
