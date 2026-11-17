import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executor import Session  # noqa: E402

TABLE = {"columns": ["id", "email", "qty"],
         "constraints": [{"name": "pk", "columns": ["id"]}, {"name": "uq_email", "columns": ["email"]}]}


class UpsertTests(unittest.TestCase):
    def test_do_nothing(self):
        s = Session(TABLE, [{"id": 1, "email": "a"}])
        r = s.execute({"op": "insert", "rows": [{"id": 1, "email": "a"}, {"id": 2, "email": "b"}],
                       "on_conflict": {"action": "nothing"}})
        self.assertEqual((r["inserted"], r["skipped"]), (1, 1))

    def test_do_update(self):
        s = Session(TABLE, [{"id": 1, "email": "a", "qty": 1}])
        s.execute({"op": "insert", "rows": [{"id": 1, "email": "a", "qty": 4}],
                   "on_conflict": {"target": "pk", "action": "update",
                                   "set": {"qty": {"add": [{"col": "qty"}, {"excluded": "qty"}]}}}})
        self.assertEqual(s.rows[1]["qty"], 5)

    def test_conflict_with_existing_row(self):
        s = Session(TABLE, [{"id": 1, "email": "a"}])
        r = s.execute({"op": "insert", "rows": [{"id": 2, "email": "b"}, {"id": 1, "email": "c"}]})
        self.assertEqual((r["ok"], r["error"]["constraint"], r["error"]["row"]), (False, "pk", 1))


if __name__ == "__main__":
    unittest.main()
