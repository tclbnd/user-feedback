import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cost  # noqa: E402
import workload  # noqa: E402

STATS = {"t": {"rows": 1000, "columns": {"a": {"ndv": 100, "width": 4}, "b": {"ndv": 10, "width": 4}},
               "indexes": []}}


class AdvisorTests(unittest.TestCase):
    def test_parse_predicates(self):
        q = workload.parse("SELECT * FROM t WHERE a = 5 AND b BETWEEN 1 AND 3 ORDER BY b DESC;")
        self.assertEqual(q["preds"], [("t", "a", "eq"), ("t", "b", "range")])
        self.assertEqual(q["order"], ["b"])

    def test_seq_scan_and_index(self):
        q = workload.parse("SELECT * FROM t WHERE a = 5")
        self.assertEqual(cost.query_cost(STATS, q, [])[0], 1000)
        ix = {"name": "ix_t_a", "table": "t", "columns": ["a"]}
        self.assertAlmostEqual(cost.query_cost(STATS, q, [ix])[0], 3 + 1.5 * 10)

    def test_index_size(self):
        self.assertEqual(cost.size(STATS, {"table": "t", "columns": ["a", "b"]}), 1000 * 16)


if __name__ == "__main__":
    unittest.main()
