import unittest

from kdsckit.voices import KdscError, parse


class BasicTests(unittest.TestCase):
    def test_no_voice_keyed(self):
        out = parse(b"K053260S" + bytes(48) + bytes(256))
        self.assertEqual(out["voices_on"], 0)
        self.assertEqual(out["voices"][0]["format"], "pcm")

    def test_not_a_snapshot(self):
        with self.assertRaises(KdscError):
            parse(b"K053260X" + bytes(64))


if __name__ == "__main__":
    unittest.main()
