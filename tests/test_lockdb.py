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

    def test_writer_blocks_reader(self):
        out = run_script({"rows": [10, 20], "steps": [
            {"tx": 1, "op": "delete", "key": 20},
            {"tx": 2, "op": "range", "low": 15, "high": 25},
            {"tx": 1, "op": "commit"}]})
        self.assertTrue(out["results"][1]["waited"])
        self.assertEqual(out["results"][1]["resumed_at"], 2)
        self.assertEqual(out["results"][1]["rows"], [])


if __name__ == "__main__":
    unittest.main()
