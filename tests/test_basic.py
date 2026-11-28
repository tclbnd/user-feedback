import unittest

from rasckit.rasc import RascError, parse


class BasicTests(unittest.TestCase):
    def test_example_packets(self):
        with open("examples/session.rasc", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual(len(out["frames"]), 4)
        self.assertEqual((out["frames"][0]["width"], out["frames"][0]["height"]), (32, 16))

    def test_bad_magic(self):
        with self.assertRaises(RascError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
