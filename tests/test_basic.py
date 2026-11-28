import unittest

from kgvkit.kgv import KgvError, parse


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/demo.kgv", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual(len(out["frames"]), 3)

    def test_bad_magic(self):
        with self.assertRaises(KgvError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
