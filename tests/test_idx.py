import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import heap  # noqa: E402
from check import Crash, check, repair  # noqa: E402

INDEXES = [{"name": "email", "column": "email", "unique": True},
           {"name": "city", "column": "city", "unique": False}]
COLUMNS = ["id", "email", "city"]
ROWS = [[1, "a@x", "Oslo"], [2, "b@x", "Lima"], [3, "c@x", "Pune"], [4, "d@x", "Kyiv"], [5, "e@x", "Riga"]]


def make(directory, rows=ROWS):
    return heap.create(directory, COLUMNS, INDEXES, rows)


def via_index(table, name, key):
    return sorted(rid for k, rid in table.entries[name] if k == key)


class IndexTests(unittest.TestCase):
    def test_write_path_keeps_indexes(self):
        with tempfile.TemporaryDirectory() as d:
            t = heap.create(d, ["id", "email", "city"], INDEXES, [[1, "a@x", "Oslo"], [2, "b@x", "Lima"]])
            t.update((0, 1), [2, "b@x", "Oslo"])
            t.delete((0, 0))
            t.save()
            t = heap.Table(d)
            self.assertEqual(t.entries["city"], [("Oslo", (0, 1))])
            self.assertEqual(check(t), [])

    def test_lost_entry_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            t = heap.create(d, ["id", "email", "city"], INDEXES, [[1, "a@x", "Oslo"]])
            t.drop_entry("email", "a@x", (0, 0))
            self.assertEqual(check(t), [{"index": "email", "kind": "missing", "key": "a@x", "rowid": [0, 0]}])

    def test_missing_is_reported_per_row(self):
        # A row whose key is in the index only for another row is still missing.
        with tempfile.TemporaryDirectory() as d:
            t = make(d, [[1, "a@x", "Oslo"], [2, "b@x", "Oslo"]])
            t.drop_entry("city", "Oslo", (0, 1))
            self.assertEqual(check(t), [{"index": "city", "kind": "missing", "key": "Oslo", "rowid": [0, 1]}])

    def test_deleted_slot_is_dangling(self):
        with tempfile.TemporaryDirectory() as d:
            t = make(d)
            t.write_slot((0, 1), heap.DEAD)  # crash lost the delete's index maintenance
            self.assertEqual(check(t), [
                {"index": "city", "kind": "dangling", "key": "Lima", "rowid": [0, 1]},
                {"index": "email", "kind": "dangling", "key": "b@x", "rowid": [0, 1]},
            ])

    def test_mismatch_and_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            t = make(d, [[1, "a@x", "Oslo"], [2, "b@x", "Lima"]])
            # crash replayed an insert: second live row holding the same unique key,
            # while the index still carries the row's old key
            t.write_slot((0, 1), heap.LIVE, json.dumps([2, "a@x", "Lima"]).encode())
            self.assertEqual(check(t), [
                {"index": "email", "kind": "duplicate", "key": "a@x", "rowid": [0, 1]},
                {"index": "email", "kind": "mismatch", "key": "b@x", "rowid": [0, 1]},
                {"index": "email", "kind": "missing", "key": "a@x", "rowid": [0, 1]},
            ])

    def corrupt(self, t):
        """Inject one problem of every kind and return the expected report."""
        t.write_slot((0, 3), heap.DEAD)                                   # dangling city+email
        t.write_slot((0, 0), heap.LIVE, json.dumps([1, "new@x", "Oslo"]).encode())  # mismatch+missing
        t.write_slot((0, 4), heap.LIVE, json.dumps([5, "b@x", "Riga"]).encode())    # duplicate+missing
        t.drop_entry("city", "Pune", (0, 2))                              # missing, key shared
        t.save()
        return [
            {"index": "city", "kind": "dangling", "key": "Kyiv", "rowid": [0, 3]},
            {"index": "city", "kind": "missing", "key": "Pune", "rowid": [0, 2]},
            {"index": "email", "kind": "dangling", "key": "d@x", "rowid": [0, 3]},
            {"index": "email", "kind": "duplicate", "key": "b@x", "rowid": [0, 4]},
            {"index": "email", "kind": "mismatch", "key": "a@x", "rowid": [0, 0]},
            {"index": "email", "kind": "mismatch", "key": "e@x", "rowid": [0, 4]},
            {"index": "email", "kind": "missing", "key": "b@x", "rowid": [0, 4]},
            {"index": "email", "kind": "missing", "key": "new@x", "rowid": [0, 0]},
        ]

    def assert_consistent(self, d):
        t = heap.Table(d)
        self.assertEqual(check(t), [])
        for ix in t.indexes:
            for key in {k for k, _ in t.entries[ix["name"]]}:
                via_idx = sorted(t.row(rid) for k, rid in t.entries[ix["name"]] if k == key)
                via_scan = sorted(row for _, row in t.rows() if t.value(row, ix) == key)
                self.assertEqual(via_idx, via_scan)

    def test_repair_fixes_every_reported_problem(self):
        with tempfile.TemporaryDirectory() as d:
            expected = self.corrupt(make(d))
            self.assertEqual(check(heap.Table(d)), expected)
            summary = repair(heap.Table(d))
            self.assertEqual(summary["plan"], 8)  # 1 quarantine + 4 drops + 3 adds
            self.assertEqual(summary["applied"] + summary["skipped"], 8)
            self.assertEqual(check(heap.Table(d)), [])
            self.assert_consistent(d)
            with open(os.path.join(d, "quarantine.jsonl"), encoding="utf-8") as fh:
                self.assertEqual([json.loads(line)["row"] for line in fh], [[5, "b@x", "Riga"]])
            with open(os.path.join(d, "repair.log"), encoding="utf-8") as fh:
                log = [json.loads(line) for line in fh]
            self.assertEqual([a["op"] for a in log if "op" in a],
                             ["quarantine"] + ["drop"] * 4 + ["add"] * 3)
            self.assertTrue(all(set(line) <= {"done", "applied", "complete"} for line in log if "op" not in line))
            self.assertTrue(log[-1]["complete"])

    def test_repair_skips_actions_made_obsolete(self):
        # The quarantined duplicate's own missing entry must not be re-added.
        with tempfile.TemporaryDirectory() as d:
            self.corrupt(make(d))
            summary = repair(heap.Table(d))
            self.assertEqual(summary["skipped"], 1)  # add b@x [0,4] after its row was quarantined
            self.assertEqual(summary["applied"], 7)

    def test_repair_with_concurrent_writes_and_crash(self):
        writes = [
            {"before_action": 1, "op": "insert", "row": [6, "f@x", "Oslo"]},
            {"before_action": 4, "op": "update", "rowid": [0, 2], "row": [3, "c@x", "Wien"]},
            {"op": "delete", "rowid": [0, 0]},
            {"op": "insert", "row": [7, "g@x", "Lima"]},
        ]
        with tempfile.TemporaryDirectory() as d:
            self.corrupt(make(d))
            with self.assertRaises(Crash):
                repair(heap.Table(d), writes, crash_after=2)
            summary = repair(heap.Table(d), writes)  # resumes the logged plan
            self.assertEqual(summary["plan"], 8)
            self.assert_consistent(d)
            t = heap.Table(d)
            rows = [row for _, row in t.rows()]
            self.assertEqual(rows.count([6, "f@x", "Oslo"]), 1)  # write not applied twice
            self.assertEqual(rows.count([7, "g@x", "Lima"]), 1)
            self.assertNotIn([1, "new@x", "Oslo"], rows)         # end-of-repair delete
            self.assertIn([3, "c@x", "Wien"], rows)              # concurrent update
            # the quarantine removed the replayed insert: only one live b@x remains
            self.assertEqual([r for r in rows if r[1] == "b@x"], [[2, "b@x", "Lima"]])


if __name__ == "__main__":
    unittest.main()
