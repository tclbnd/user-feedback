import unittest

from jsonidx.path import matches, produce, parse
from jsonidx.table import DocTable


class PathTests(unittest.TestCase):
    def test_produce(self):
        doc = {"a": [{"b": 1}, {"b": 2}, {"c": 3}]}
        self.assertEqual(produce(doc, parse("$.a[*].b")), [1, 2])
        self.assertEqual(produce(doc, parse("$.a[1].b")), [2])
        self.assertEqual(produce(doc, parse("$.x")), [])

    def test_equality_semantics(self):
        self.assertTrue(matches({"n": 1.0}, {"path": "$.n", "op": "=", "value": 1}))
        self.assertFalse(matches({"n": True}, {"path": "$.n", "op": "=", "value": 1}))

    def test_index_on_nested_scalar(self):
        t = DocTable()
        t.insert("a", {"stock": {"eu": 3}})
        t.insert("b", {"stock": {"us": 3}})
        q = t.query({"path": "$.stock.eu", "op": "=", "value": 3})
        self.assertEqual((q["plan"], q["ids"]), ("index", ["a"]))


if __name__ == "__main__":
    unittest.main()
