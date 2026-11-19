import unittest

from jsonbx.ops import contains, exists
from jsonbx.table import DocTable
from jsonbx.value import parse


class OpsTests(unittest.TestCase):
    def test_object_containment(self):
        self.assertTrue(contains(parse('{"a": 1, "b": {"c": "x"}}'), parse('{"b": {"c": "x"}}')))
        self.assertFalse(contains(parse('{"a": 1}'), parse('{"a": 2}')))

    def test_key_exists(self):
        self.assertTrue(exists(parse('{"a": 1}'), "a"))

    def test_index_query_rechecks(self):
        t = DocTable(("jsonb_ops", "jsonb_path_ops"))
        t.insert("r1", '{"a": "x", "b": "y"}')
        t.insert("r2", '{"a": "y", "b": "x"}')
        for name in ("jsonb_ops", "jsonb_path_ops"):
            self.assertEqual(t.query("@>", parse('{"a": "x"}'), name)["ids"], ["r1"])


if __name__ == "__main__":
    unittest.main()
