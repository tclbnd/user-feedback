import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import storage  # noqa: E402


class PoolTests(unittest.TestCase):
    def test_page_roundtrip(self):
        self.assertEqual(storage.decode(storage.encode(7, ["a", "", "bc", "x" * 13])), (7, ["a", "", "bc", "x" * 13]))

    def test_committed_writes_survive(self):
        trace = [{"op": "write", "page": p, "slot": 0, "value": f"v{p}"} for p in range(3)] + [{"op": "commit"}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.json")
            with open(path, "w") as fh:
                json.dump(trace, fh)
            bp = os.path.join(ROOT, "bp.py")
            subprocess.run([sys.executable, bp, "run", d, path], check=True, capture_output=True)
            subprocess.run([sys.executable, bp, "recover", d], check=True, capture_output=True)
            disk = storage.Disk(os.path.join(d, "data.db"))
            self.assertEqual([disk.read(p)[1][0] for p in range(3)], ["v0", "v1", "v2"])


if __name__ == "__main__":
    unittest.main()
