import unittest

from argokit.argo import ArgoError, parse


class BasicTests(unittest.TestCase):
    def test_example_chunks(self):
        with open("examples/movie.brp", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (64, 32, 5))

    def test_bad_magic(self):
        with self.assertRaises(ArgoError):
            parse(bytes(16))


if __name__ == "__main__":
    unittest.main()
