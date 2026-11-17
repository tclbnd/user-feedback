import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from query import run  # noqa: E402

DB = {
    "customers": {"rows": [{"id": 1}, {"id": 2}]},
    "orders": {"rows": [{"cust": 1, "amount": 5}, {"cust": 1, "amount": 7}, {"cust": 2, "amount": 4}]},
}
TOTAL = {"scalar": {"from": "orders", "as": "o", "agg": "sum", "col": "o.amount",
                    "where": [{"op": "=", "left": {"col": "o.cust"}, "right": {"col": "c.id"}}]}}
QUERY = {"from": "customers", "as": "c", "select": [{"col": "c.id"}, TOTAL], "where": []}


class QueryTests(unittest.TestCase):
    def test_row_by_row(self):
        out = run(DB, QUERY, rewrite=False)
        self.assertEqual(out["rows"], [[1, 12], [2, 4]])
        self.assertEqual(out["plan"], ["SubPlan o"])
        self.assertEqual(out["subplan_runs"], 2)

    def test_equality_correlation_is_decorrelated(self):
        out = run(DB, QUERY)
        self.assertEqual(out, {"plan": ["Decorrelated o"], "rows": [[1, 12], [2, 4]], "subplan_runs": 0})


if __name__ == "__main__":
    unittest.main()
