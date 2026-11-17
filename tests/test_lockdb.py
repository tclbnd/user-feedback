import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import run_script  # noqa: E402


class LockdbTests(unittest.TestCase):
    def test_insert_commit_then_scan(self):
        out = run_script({"rows": [10], "steps": [
            {"tx": 1, "op": "insert", "key": 5}, {"tx": 1, "op": "commit"},
            {"tx": 2, "op": "range", "low": None, "high": None}]})
        self.assertEqual(out["results"][2]["rows"], [5, 10])
        self.assertEqual(out["table"], [5, 10])

    def test_duplicate_key(self):
        out = run_script({"rows": [10, 20], "steps": [{"tx": 1, "op": "insert", "key": 20}]})
        self.assertEqual(out["results"][0]["status"], "error")

    def test_duplicate_error_releases_statement_lock(self):
        from engine import Engine

        engine = Engine([10, 20])
        out = engine.insert(1, 20)
        self.assertEqual(out["error"], "duplicate key 20")
        self.assertEqual(engine.locks.locks, set())

    def test_writer_blocks_reader(self):
        out = run_script({"rows": [10, 20], "steps": [
            {"tx": 1, "op": "delete", "key": 20},
            {"tx": 2, "op": "range", "low": 15, "high": 25},
            {"tx": 1, "op": "commit"}]})
        self.assertTrue(out["results"][1]["waited"])
        self.assertEqual(out["results"][1]["resumed_at"], 2)
        self.assertEqual(out["results"][1]["rows"], [])

    def test_full_range_locks_gap_before_first_and_supremum(self):
        out = run_script({"unique": True, "rows": [10, 20], "steps": [
            {"tx": 1, "op": "range", "low": None, "high": None},
            {"tx": 2, "op": "insert", "key": 5},
            {"tx": 3, "op": "insert", "key": 15},
            {"tx": 4, "op": "insert", "key": 25},
            {"tx": 1, "op": "rollback"},
            {"tx": 2, "op": "rollback"},
            {"tx": 3, "op": "rollback"},
            {"tx": 4, "op": "rollback"}]})
        results = out["results"]
        self.assertEqual(results[0]["rows"], [10, 20])
        for step in (1, 2, 3):
            self.assertTrue(results[step]["waited"])
            self.assertEqual(results[step]["resumed_at"], 4)

    def test_range_locks_phantoms_and_boundary_gaps(self):
        out = run_script({"unique": True, "rows": [10, 20, 30, 40], "steps": [
            {"tx": 1, "op": "range", "low": 15, "high": 32},
            {"tx": 2, "op": "insert", "key": 25},   # gap inside range
            {"tx": 3, "op": "insert", "key": 35},   # gap just past range end
            {"tx": 4, "op": "insert", "key": 15},   # open lower-bound gap
            {"tx": 5, "op": "insert", "key": 10},   # outside the scanned range
            {"tx": 1, "op": "range", "low": 15, "high": 32},
            {"tx": 1, "op": "rollback"},
            {"tx": 2, "op": "rollback"},
            {"tx": 3, "op": "rollback"},
            {"tx": 4, "op": "rollback"},
            {"tx": 5, "op": "rollback"}]})
        results = out["results"]
        self.assertEqual(results[0]["rows"], [20, 30])
        for step in (1, 2, 3):
            self.assertTrue(results[step]["waited"])
            self.assertEqual(results[step]["resumed_at"], 6)
        self.assertFalse(results[4]["waited"])
        self.assertEqual(results[5]["rows"], [20, 30])
        self.assertEqual(out["table"], [10, 20, 30, 40])

    def test_unique_equality_hit_only_locks_record(self):
        out = run_script({"unique": True, "rows": [10, 20, 30], "steps": [
            {"tx": 1, "op": "get", "key": 20},
            {"tx": 2, "op": "insert", "key": 15},
            {"tx": 3, "op": "get", "key": 20, "for_update": True},
            {"tx": 1, "op": "rollback"},
            {"tx": 2, "op": "rollback"},
            {"tx": 3, "op": "rollback"}]})
        results = out["results"]
        self.assertEqual(results[0]["rows"], [20])
        self.assertFalse(results[1]["waited"])
        self.assertTrue(results[2]["waited"])
        self.assertEqual(results[2]["resumed_at"], 3)

    def test_unique_equality_miss_locks_gap_and_gap_reads_compose(self):
        out = run_script({"unique": True, "rows": [10, 20, 30], "steps": [
            {"tx": 1, "op": "get", "key": 15},
            {"tx": 2, "op": "get", "key": 17},
            {"tx": 3, "op": "insert", "key": 16},
            {"tx": 1, "op": "rollback"},
            {"tx": 2, "op": "rollback"},
            {"tx": 3, "op": "rollback"}]})
        results = out["results"]
        self.assertEqual(results[0]["rows"], [])
        self.assertFalse(results[1]["waited"])  # gap locks are compatible
        self.assertTrue(results[2]["waited"])
        self.assertEqual(results[2]["resumed_at"], 4)
        self.assertEqual(out["table"], [10, 20, 30])

    def test_nonunique_equality_uses_next_key_and_following_gap(self):
        out = run_script({"unique": False, "rows": [10, 20, 20, 30], "steps": [
            {"tx": 1, "op": "get", "key": 20},
            {"tx": 2, "op": "insert", "key": 20},
            {"tx": 3, "op": "insert", "key": 25},
            {"tx": 4, "op": "insert", "key": 15},
            {"tx": 1, "op": "rollback"},
            {"tx": 2, "op": "rollback"},
            {"tx": 3, "op": "rollback"},
            {"tx": 4, "op": "rollback"}]})
        results = out["results"]
        self.assertEqual(results[0]["rows"], [20, 20])
        for step in (1, 2, 3):
            self.assertTrue(results[step]["waited"])
            self.assertEqual(results[step]["resumed_at"], 4)

    def test_delete_then_insert_waits_for_record_lock_and_commits(self):
        out = run_script({"unique": True, "rows": [10, 20, 30], "steps": [
            {"tx": 1, "op": "delete", "key": 20},
            {"tx": 2, "op": "insert", "key": 20},
            {"tx": 1, "op": "commit"},
            {"tx": 2, "op": "commit"}]})
        results = out["results"]
        self.assertEqual(results[0]["count"], 1)
        self.assertTrue(results[1]["waited"])
        self.assertEqual(results[1]["resumed_at"], 2)
        self.assertEqual(results[1]["status"], "ok")
        self.assertEqual(results[1]["count"], 1)
        self.assertEqual(out["table"], [10, 20, 30])

    def test_gap_locks_merge_after_physical_delete(self):
        out = run_script({"unique": True, "rows": [10, 20, 30], "steps": [
            {"tx": 1, "op": "get", "key": 15},
            {"tx": 2, "op": "delete", "key": 20},
            {"tx": 2, "op": "commit"},       # physically removes 20 and merges gaps
            {"tx": 3, "op": "insert", "key": 25},
            {"tx": 1, "op": "rollback"},
            {"tx": 3, "op": "rollback"}]})
        results = out["results"]
        self.assertFalse(results[1]["waited"])
        self.assertTrue(results[3]["waited"])  # the old 15-gap now spans to 30
        self.assertEqual(results[3]["resumed_at"], 4)
        self.assertEqual(out["table"], [10, 30])

    def test_gap_lock_skips_uncommitted_insert_when_merging(self):
        out = run_script({"unique": True, "rows": [10, 20, 30, 40], "steps": [
            {"tx": 1, "op": "delete", "key": 20},
            {"tx": 2, "op": "insert", "key": 20},
            {"tx": 1, "op": "commit"},
            {"tx": 3, "op": "get", "key": 15},
            {"tx": 4, "op": "delete", "key": 30},
            {"tx": 4, "op": "commit"},
            {"tx": 5, "op": "insert", "key": 35},
            {"tx": 3, "op": "rollback"},
            {"tx": 2, "op": "commit"},
            {"tx": 5, "op": "rollback"}]})
        results = out["results"]
        self.assertTrue(results[6]["waited"])
        self.assertEqual(results[6]["resumed_at"], 7)
        self.assertEqual(out["table"], [10, 20, 40])


if __name__ == "__main__":
    unittest.main()
