import unittest

from cancel.batch import failed_records, settlement_script
from cancel.scheduler import run


class BatchTests(unittest.TestCase):
    def test_batch_commits_all_records(self):
        recs = [("a", 1), ("b", 2)]
        out = run({"data": {"t": {"a": 0, "b": 0}}, "sessions": {"batch": {"script": settlement_script("t", recs)}}})
        self.assertEqual(out["data"]["t"], {"a": 1, "b": 2})
        self.assertEqual(failed_records(out["results"]["batch"], recs), [])
        self.assertEqual(out["locks"], {})

    def test_rollback_to_undoes_later_writes(self):
        script = [{"cmd": "begin"}, {"cmd": "update", "table": "t", "key": "a", "value": 1, "work": 0},
                  {"cmd": "savepoint", "name": "p"}, {"cmd": "update", "table": "t", "key": "a", "value": 2, "work": 0},
                  {"cmd": "rollback_to", "name": "p"}, {"cmd": "commit"}]
        out = run({"data": {"t": {"a": 0}}, "sessions": {"s": {"script": script}}})
        self.assertEqual(out["data"]["t"]["a"], 1)


if __name__ == "__main__":
    unittest.main()
