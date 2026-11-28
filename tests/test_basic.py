import unittest

from mmvkit.mmv import MmvError, parse


class BasicTests(unittest.TestCase):
    def test_example_packets(self):
        with open("examples/scene.mm", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual([f["state"] for f in out["frames"]][:2], ["palette", "decoded"])

    def test_bad_magic(self):
        with self.assertRaises(MmvError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
