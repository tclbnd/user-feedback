import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from table import Database  # noqa: E402


class OscTests(unittest.TestCase):
    def test_trigger_and_lock(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "db.json")
            with open(path, "w") as fh:
                json.dump({"tables": {"t": {"pk": "id", "columns": ["id", "v"], "rows": [{"id": 1, "v": "a"}]},
                                      "log": {"pk": "seq", "columns": ["seq", "op", "pk", "old_pk", "row"], "rows": []}},
                           "triggers": [{"name": "trg", "table": "t", "log": "log"}]}, fh)
            db = Database(path, [{"tick": 1, "op": "insert", "table": "t", "row": {"id": 2, "v": "b"}}])
            db.lock("t")
            db.tick()
            self.assertNotIn(2, db.tables["t"]["rows"])
            db.unlock("t")
            self.assertEqual([r["op"] for r in db.tables["log"]["rows"].values()], ["insert"])

    def test_migrate_quiet_table(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.json")
            shutil.copy(os.path.join(ROOT, "examples", "users.json"), db)
            proc = subprocess.run([sys.executable, os.path.join(ROOT, "osc.py"), "migrate", db,
                                   os.path.join(ROOT, "examples", "alter.json")], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(db) as fh:
                data = json.load(fh)
            self.assertEqual(sorted(data["tables"]), ["users"])
            self.assertEqual(data["tables"]["users"]["columns"], ["id", "name", "tier"])
            self.assertEqual(len(data["tables"]["users"]["rows"]), 12)


if __name__ == "__main__":
    unittest.main()
