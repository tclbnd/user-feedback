import unittest

from mvckit.mvc import MvcError, parse


class BasicTests(unittest.TestCase):
    def test_example_codec(self):
        with open("examples/studio.mvc", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["codec"], len(out["frames"])), ("MVC1", 1))

    def test_bad_magic(self):
        with self.assertRaises(MvcError):
            parse(bytes(32))


if __name__ == "__main__":
    unittest.main()
