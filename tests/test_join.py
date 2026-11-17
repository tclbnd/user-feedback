import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDERS = [{"oid": 1, "cid": 10}, {"oid": 2, "cid": 11}, {"oid": 3, "cid": 10}, {"oid": 4, "cid": 99}]
CUSTOMERS = [{"id": 10, "name": "ann"}, {"id": 11, "name": "bo"}, {"id": 12, "name": "cy"}]


def run_join(left, right, kind):
    with tempfile.TemporaryDirectory() as d:
        paths = [os.path.join(d, n) for n in ("l.jsonl", "r.jsonl", "o.jsonl")]
        for path, rows in zip(paths, (left, right)):
            with open(path, "w") as fh:
                fh.writelines(json.dumps(r) + "\n" for r in rows)
        subprocess.run([sys.executable, os.path.join(ROOT, "join.py"), *paths, "--type", kind,
                        "--on", "cid=id"], check=True)
        with open(paths[2]) as fh:
            return [json.loads(line) for line in fh]


class JoinTests(unittest.TestCase):
    def test_inner(self):
        out = run_join(ORDERS, CUSTOMERS, "inner")
        self.assertEqual(sorted((r["l"]["oid"], r["r"]["name"]) for r in out), [(1, "ann"), (2, "bo"), (3, "ann")])

    def test_left_keeps_unmatched(self):
        out = run_join(ORDERS, CUSTOMERS, "left")
        self.assertEqual(len(out), 4)
        self.assertIn({"l": {"oid": 4, "cid": 99}, "r": None}, out)

    def test_integer_and_string_keys_differ(self):
        self.assertEqual(run_join([{"oid": 1, "cid": "10"}], CUSTOMERS, "inner"), [])


if __name__ == "__main__":
    unittest.main()
