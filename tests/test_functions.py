import unittest

from bincall import call


def value(*c):
    return call(list(c))["value"]


class FunctionTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(value("encode", "\\x0001ff", "hex"), "0001ff")
        self.assertEqual(value("decode", "MTIz", "base64"), "\\x313233")
        self.assertEqual(value("length", "\\x123456"), "3")
        self.assertEqual(value("||", "\\x1234", "\\x56"), "\\x123456")
        self.assertEqual(value("bit_count", "B'10110'"), "3")
        self.assertIsNone(value("length", None))


if __name__ == "__main__":
    unittest.main()
