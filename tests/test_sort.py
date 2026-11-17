import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import keys  # noqa: E402


def run_sort(rows, *args):
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.jsonl"), os.path.join(d, "out.jsonl")
        with open(src, "w") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in rows)
        subprocess.run([sys.executable, os.path.join(ROOT, "sort.py"), src, dst, *args], check=True)
        with open(dst) as fh:
            return [json.loads(line) for line in fh]


class SortTests(unittest.TestCase):
    def test_parse(self):
        k = keys.parse("a, b DESC NULLS last, c collate NOCASE")
        self.assertEqual([x["col"] for x in k], ["a", "b", "c"])
        self.assertTrue(k[1]["desc"])
        self.assertEqual(k[2]["collate"], "nocase")

    def test_numbers_before_strings(self):
        out = run_sort([{"v": "b"}, {"v": 3}, {"v": "a"}, {"v": 1}], "--order", "v")
        self.assertEqual([r["v"] for r in out], [1, 3, "a", "b"])

    def test_in_memory_ascending(self):
        rows = [{"id": i, "v": (i * 7) % 11} for i in range(30)]
        out = run_sort(rows, "--order", "v")
        self.assertEqual([r["v"] for r in out], sorted(r["v"] for r in rows))


if __name__ == "__main__":
    unittest.main()
