import unittest

from vockit.voc import scan

from vocs import pcm8, sound, voc


class BasicTests(unittest.TestCase):
    def test_single_block(self):
        out = scan(voc([sound(156, pcm8([0, 10, -10, 20] * 25))]))
        self.assertEqual(out["version"], "1.10")
        self.assertEqual(out["sample_rate"], 10000.0)
        self.assertEqual(out["duration"], "1/100")
        self.assertEqual(out["blocks"], {"1": 1})


if __name__ == "__main__":
    unittest.main()
