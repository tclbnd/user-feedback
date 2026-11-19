import unittest

from csvcopy.loader import CopyError, copy_from

T = {"columns": [{"name": "id", "type": "int"}, {"name": "v", "type": "text"}]}


class BasicTests(unittest.TestCase):
    def test_simple_rows(self):
        self.assertEqual(copy_from(T, "1,a\n2,b\n"), [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])

    def test_bad_int(self):
        with self.assertRaises(CopyError):
            copy_from(T, "x,a\n")


if __name__ == "__main__":
    unittest.main()
