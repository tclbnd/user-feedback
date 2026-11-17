import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wal  # noqa: E402

CLUSTER = os.path.join(ROOT, "examples", "cluster")


class PitrTests(unittest.TestCase):
    def test_reads_all_segments(self):
        records = wal.read(os.path.join(CLUSTER, "wal"))
        self.assertEqual(sorted(records), list(range(1, 34)))

    def test_restore_to_end_of_log(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "db")
            proc = subprocess.run([sys.executable, os.path.join(ROOT, "pitr.py"), "restore", CLUSTER, out,
                                   "--to-time", "5000"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            with open(os.path.join(out, "data.json")) as fh:
                tables = json.load(fh)["tables"]
            self.assertEqual(tables["accounts"]["alice"], "75")
            self.assertEqual(tables["accounts"]["erin"], "10")
            self.assertNotIn("orders", tables)


if __name__ == "__main__":
    unittest.main()
