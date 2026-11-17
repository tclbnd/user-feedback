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

    def test_batch_internal_duplicate_reports_second_row(self):
        s = Session(TABLE)
        r = s.execute({"op": "insert", "rows": [{"id": 5, "email": "a"},
                                                {"id": 6, "email": "b"},
                                                {"id": 5, "email": "c"}]})
        self.assertEqual((r["ok"], r["error"]["constraint"], r["error"]["key"], r["error"]["row"]),
                         (False, "pk", [5], 2))
        self.assertEqual(s.rows, {})  # failed statement is fully undone

    def test_batch_internal_duplicate_do_nothing_skips(self):
        s = Session(TABLE)
        r = s.execute({"op": "insert", "rows": [{"id": 5, "email": "a"}, {"id": 5, "email": "b"}],
                       "on_conflict": {"action": "nothing"}})
        self.assertEqual((r["inserted"], r["skipped"]), (1, 1))

    def test_cardinality_violation_on_second_hit(self):
        s = Session(TABLE)
        r = s.execute({"op": "insert", "rows": [{"id": 7, "email": "a", "qty": 1},
                                                {"id": 7, "email": "a", "qty": 4}],
                       "on_conflict": {"target": "pk", "action": "update",
                                       "set": {"qty": {"excluded": "qty"}}}})
        self.assertEqual((r["ok"], r["error"]["code"], r["error"]["row"]),
                         (False, "cardinality_violation", 1))
        self.assertEqual(s.rows, {})

    def test_constraints_decide_in_definition_order(self):
        s = Session(TABLE, [{"id": 1, "email": "a"}, {"id": 2, "email": "b"}])
        # collides with pk of row 1 and uq_email of row 2: pk comes first
        r = s.execute({"op": "insert", "rows": [{"id": 1, "email": "b"}]})
        self.assertEqual((r["error"]["constraint"], r["error"]["key"]), ("pk", [1]))
        # target naming uq_email does not excuse the pk collision
        r = s.execute({"op": "insert", "rows": [{"id": 1, "email": "b"}],
                       "on_conflict": {"target": "uq_email", "action": "nothing"}})
        self.assertEqual((r["ok"], r["error"]["constraint"]), (False, "pk"))

    def test_swap_primary_keys_in_one_update(self):
        s = Session(TABLE, [{"id": 1, "email": "a"}, {"id": 2, "email": "b"}])
        r = s.execute({"op": "update", "where": {"id": {"in": [1, 2]}},
                       "set": {"id": {"sub": [3, {"col": "id"}]}}})
        self.assertEqual((r["ok"], r["updated"]), (True, 2))
        self.assertEqual([row["id"] for row in s.rows.values()], [2, 1])

    def test_deferred_constraint_checked_at_commit(self):
        table = {"columns": ["id", "email"],
                 "constraints": [{"name": "uq_email", "columns": ["email"],
                                  "deferrable": True, "initially": "deferred"}]}
        s = Session(table, [{"id": 1, "email": "a"}, {"id": 2, "email": "b"}])
        self.assertTrue(s.execute({"op": "begin"})["ok"])
        # intermediate collision is fine while the constraint is deferred
        self.assertTrue(s.execute({"op": "update", "where": {"id": 1},
                                   "set": {"email": "b"}})["ok"])
        r = s.execute({"op": "commit"})
        self.assertEqual((r["ok"], r["error"]["at"], r["error"]["constraint"], r["error"]["row"]),
                         (False, "commit", "uq_email", None))
        # commit failure rolls back the whole transaction
        self.assertEqual([row["email"] for row in s.rows.values()], ["a", "b"])

    def test_failed_statement_does_not_abort_transaction(self):
        s = Session(TABLE, [{"id": 1, "email": "a"}])
        s.execute({"op": "begin"})
        s.execute({"op": "insert", "rows": [{"id": 2, "email": "b"}]})
        r = s.execute({"op": "insert", "rows": [{"id": 3, "email": "c"}, {"id": 1, "email": "x"}]})
        self.assertEqual((r["ok"], r["error"]["row"]), (False, 1))
        s.execute({"op": "insert", "rows": [{"id": 4, "email": "d"}]})
        self.assertTrue(s.execute({"op": "commit"})["ok"])
        # the failed statement left no trace; the others survived
        self.assertEqual([row["id"] for row in s.rows.values()], [1, 2, 4])

    def test_invalid_target_when_not_immediate(self):
        table = {"columns": ["id"], "constraints": [{"name": "pk", "columns": ["id"],
                                                     "deferrable": True, "initially": "deferred"}]}
        s = Session(table, [{"id": 1}])
        r = s.execute({"op": "insert", "rows": [{"id": 1}],
                       "on_conflict": {"target": "pk", "action": "update", "set": {}}})
        self.assertEqual((r["ok"], r["error"]["code"]), (False, "invalid_target"))


if __name__ == "__main__":
    unittest.main()
