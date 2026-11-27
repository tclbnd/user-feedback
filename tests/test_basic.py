import unittest

from c140kit.voices import C140Error, parse


class BasicTests(unittest.TestCase):
    def test_no_voice_keyed(self):
        out = parse(b"C140SNAP" + bytes(24 * 16) + bytes(512))
        self.assertEqual(out["voices_keyed"], 0)
        self.assertEqual(out["rom_words"], 256)

    def test_not_a_snapshot(self):
        with self.assertRaises(C140Error):
            parse(b"C140SNAX" + bytes(64))


if __name__ == "__main__":
    unittest.main()
