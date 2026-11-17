import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import Engine  # noqa: E402


class EngineTests(unittest.TestCase):
    def test_snapshot_read(self):
        e = Engine({"1": "a"})
        r, w = e.begin("R"), e.begin("W")
        e.write(w, 1, "b")
        e.commit(w)
        self.assertEqual(e.read(r, 1), "a")
        self.assertEqual(e.read(e.begin("N"), 1), "b")

    def test_first_updater_wins(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            ops = [{"op": "begin", "tx": "A"}, {"op": "begin", "tx": "B"},
                   {"op": "put", "tx": "A", "key": 1, "value": 1}, {"op": "put", "tx": "B", "key": 1, "value": 2},
                   {"op": "commit", "tx": "A"}]
            with open(path, "w") as fh:
                json.dump({"ops": ops}, fh)
            out = json.loads(subprocess.run([sys.executable, os.path.join(ROOT, "ssidb.py"), "run", path],
                                            capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["results"][3]["code"], "40001")
        self.assertEqual(out["committed"], ["A"])


if __name__ == "__main__":
    unittest.main()
