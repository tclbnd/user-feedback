import unittest

from objkit.cps1obj import ObjError, parse


class BasicTests(unittest.TestCase):
    def test_empty_table(self):
        out = parse(b"CPS1OBJ\0" + bytes(2048) + bytes(128))
        self.assertEqual(out["entries"], 256)
        self.assertIsNone(out["end_marker"])

    def test_partial_tile(self):
        with self.assertRaises(ObjError):
            parse(b"CPS1OBJ\0" + bytes(2048) + bytes(100))


if __name__ == "__main__":
    unittest.main()
