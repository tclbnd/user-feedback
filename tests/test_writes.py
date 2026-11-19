import unittest

from jsonidx.path import matches
from jsonidx.table import DocTable


class WriteTests(unittest.TestCase):
    def test_range_semantics(self):
        self.assertTrue(matches({"p": 25}, {"path": "$.p", "op": ">", "value": 20}))
        self.assertFalse(matches({"p": "25"}, {"path": "$.p", "op": ">", "value": 20}))
        self.assertFalse(matches({"p": True}, {"path": "$.p", "op": ">=", "value": 1}))

    def test_replace_and_delete_scan(self):
        t = DocTable()
        t.insert("a", {"n": 1})
        t.replace("a", {"n": 2})
        t.insert("b", {"n": 1})
        t.delete("b")
        self.assertEqual(t.scan({"path": "$.n", "op": "=", "value": 2}), ["a"])
        self.assertEqual(t.scan({"path": "$.n", "op": "=", "value": 1}), [])


if __name__ == "__main__":
    unittest.main()
