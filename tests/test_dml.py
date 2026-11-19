import unittest

from rowexec.dml import DmlError, Session
from rowexec.schema import Table

SPEC = {"name": "t", "key": "id", "columns": [{"name": "id"}, {"name": "a", "default": 2}, {"name": "b"},
                                               {"name": "s", "generated": {"add": [{"col": "a"}, {"col": "b"}]}}]}


class DmlTests(unittest.TestCase):
    def test_default_and_generated(self):
        s = Session(Table(SPEC))
        row = s.insert([{"id": 1, "b": 3}])[0]
        self.assertEqual((row["a"], row["s"]), (2, 5))

    def test_update_recomputes_generated(self):
        s = Session(Table(SPEC))
        s.insert([{"id": 1, "a": 1, "b": 1}])
        row = s.update([1], {"b": 10})[0]
        self.assertEqual(row["s"], 11)

    def test_duplicate_key(self):
        s = Session(Table(SPEC))
        s.insert([{"id": 1, "b": 1}])
        with self.assertRaises(DmlError):
            s.insert([{"id": 1, "b": 2}])


if __name__ == "__main__":
    unittest.main()
