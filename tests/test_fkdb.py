import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions import execute  # noqa: E402
from catalog import Database  # noqa: E402


def db(on_delete):
    return Database({"tables": {"p": {"pk": "id"}, "c": {"pk": "id"}},
                     "rows": {"p": [{"id": 1}, {"id": 2}], "c": [{"id": 7, "p": 1}, {"id": 8, "p": 2}]},
                     "foreign_keys": [{"name": "fk", "table": "c", "column": "p", "ref_table": "p",
                                       "ref_column": "id", "on_delete": on_delete}]})


class FkTests(unittest.TestCase):
    def test_cascade_delete(self):
        d = db("cascade")
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r["deleted"], {"c": 1, "p": 1})
        self.assertEqual(sorted(d.rows["c"]), [8])

    def test_set_null(self):
        d = db("set_null")
        execute(d, {"op": "delete", "table": "p", "where": {"id": 2}})
        self.assertIsNone(d.rows["c"][8]["p"])

    def test_restrict_keeps_tables(self):
        d = db("restrict")
        r = execute(d, {"op": "delete", "table": "p", "where": {}})
        self.assertFalse(r["ok"])
        self.assertEqual(sorted(d.rows["p"]), [1, 2])


if __name__ == "__main__":
    unittest.main()
