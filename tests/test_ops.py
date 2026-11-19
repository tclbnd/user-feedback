import unittest

from jsonbx.ops import contains, exists, exists_all, exists_any
from jsonbx.table import DocTable
from jsonbx.value import parse


class OpsTests(unittest.TestCase):
    def test_object_containment(self):
        self.assertTrue(contains(parse('{"a": 1, "b": {"c": "x"}}'), parse('{"b": {"c": "x"}}')))
        self.assertFalse(contains(parse('{"a": 1}'), parse('{"a": 2}')))

    def test_array_containment_is_unordered(self):
        self.assertTrue(contains(parse('["red", "cotton"]'), parse('["cotton", "red"]')))
        self.assertTrue(contains(parse('["red", "cotton", "blue"]'), parse('["blue", "red"]')))
        self.assertTrue(contains(parse('[1]'), parse('[1, 1]')))
        self.assertTrue(contains(parse('[1, 2]'), parse('[]')))
        self.assertFalse(contains(parse('["red"]'), parse('["red", "blue"]')))

    def test_array_contains_non_array_element(self):
        self.assertTrue(contains(parse('[1, 2]'), parse('1')))
        self.assertTrue(contains(parse('[{"a": 1}]'), parse('{"a": 1}')))
        self.assertFalse(contains(parse('{"a": 1}'), parse('1')))

    def test_numbers_compare_by_value(self):
        self.assertEqual(parse('20'), parse('20.0'))
        self.assertEqual(parse('2e1'), parse('20'))
        self.assertEqual(parse('-0'), parse('0'))
        self.assertNotEqual(parse('20'), parse('20.1'))
        self.assertTrue(contains(parse('{"price": 20.0}'), parse('{"price": 20}')))
        self.assertTrue(contains(parse('[20.0]'), parse('[20]')))

    def test_duplicate_keys_keep_last(self):
        self.assertEqual(parse('{"a": 1, "a": 2}'), parse('{"a": 2}'))

    def test_key_exists(self):
        self.assertTrue(exists(parse('{"a": 1}'), "a"))

    def test_exists_is_top_level_only(self):
        self.assertFalse(exists(parse('{"stock": {"eu": 3}}'), "eu"))
        self.assertTrue(exists(parse('{"stock": {"eu": 3}}'), "stock"))
        self.assertFalse(exists(parse('{"a": [{"b": 1}]}'), "b"))

    def test_exists_matches_string_array_elements(self):
        self.assertTrue(exists(parse('["red", "cotton"]'), "red"))
        self.assertFalse(exists(parse('[["red"]]'), "red"))
        self.assertFalse(exists(parse('[1, 2]'), "1"))

    def test_exists_any_all(self):
        doc = parse('{"a": 1, "b": 2}')
        self.assertTrue(exists_any(doc, ["a", "z"]))
        self.assertFalse(exists_any(doc, ["y", "z"]))
        self.assertTrue(exists_all(doc, ["a", "b"]))
        self.assertFalse(exists_all(doc, ["a", "z"]))

    def test_index_query_rechecks(self):
        t = DocTable(("jsonb_ops", "jsonb_path_ops"))
        t.insert("r1", '{"a": "x", "b": "y"}')
        t.insert("r2", '{"a": "y", "b": "x"}')
        for name in ("jsonb_ops", "jsonb_path_ops"):
            self.assertEqual(t.query("@>", parse('{"a": "x"}'), name)["ids"], ["r1"])


class IndexConsistencyTests(unittest.TestCase):
    """Every plan (jsonb_ops, jsonb_path_ops, seq scan) must agree."""

    DOCS = {
        "p1": '{"name": "shirt", "tags": ["red", "cotton"], "price": 20, "stock": {"eu": 3, "us": 0}}',
        "p2": '{"name": "mug", "tags": ["cotton", "red"], "price": 20.0}',
        "p3": '{"name": "cap", "tags": ["blue"], "price": 15, "sizes": ["S", "M"]}',
        "p4": '{"name": "sock", "tags": ["red"], "price": 5, "stock": {"eu": 0}}',
    }
    QUERIES = [
        ("@>", parse('{"tags": ["red", "cotton"]}')),
        ("@>", parse('{"tags": ["cotton", "red"]}')),
        ("@>", parse('{"price": 20}')),
        ("@>", parse('{"price": 20.0}')),
        ("@>", parse('{"stock": {"eu": 3}}')),
        ("@>", parse('{"stock": {}}')),
        ("@>", parse('{}')),
        ("@>", parse('[]')),
        ("@>", parse('{"tags": []}')),
        ("@>", parse('"shirt"')),
        ("@>", parse('["red"]')),
        ("?", "stock"),
        ("?", "eu"),
        ("?", "red"),
        ("?|", ["stock", "sizes"]),
        ("?&", ["stock", "eu"]),
        ("?&", []),
        ("?|", []),
    ]

    def setUp(self):
        self.table = DocTable(("jsonb_ops", "jsonb_path_ops"))
        for rid, text in self.DOCS.items():
            self.table.insert(rid, text)

    def test_all_plans_agree(self):
        for op, arg in self.QUERIES:
            with self.subTest(op=op, arg=arg):
                expected = self.table.scan(op, arg)
                for name in self.table.indexes:
                    self.assertEqual(self.table.query(op, arg, name)["ids"], expected)
                self.assertEqual(self.table.query(op, arg)["ids"], expected)

    def test_reported_bugs(self):
        # tag order must not matter
        self.assertEqual(self.table.scan("@>", parse('{"tags": ["red", "cotton"]}')), ["p1", "p2"])
        # 20 and 20.0 are the same number
        self.assertEqual(self.table.scan("@>", parse('{"price": 20}')), ["p1", "p2"])
        # ? looks at the top level only
        self.assertEqual(self.table.scan("?", "eu"), [])
        self.assertEqual(self.table.scan("?", "stock"), ["p1", "p4"])


if __name__ == "__main__":
    unittest.main()
