import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from query import run  # noqa: E402

DB = {
    "orders": {"not_null": ["id", "cust"], "rows": [{"id": 1, "cust": 7}, {"id": 2, "cust": 8}, {"id": 3, "cust": 9}]},
    "vip": {"not_null": ["cust"], "rows": [{"cust": 7}, {"cust": 9}]},
}


def query(op):
    pred = {"op": op, "args": [{"col": "o.cust"}], "query": {"from": "vip", "as": "v", "select": ["v.cust"]}}
    return {"select": ["o.id"], "from": "orders", "as": "o", "where": [pred]}


class QueryTests(unittest.TestCase):
    def test_in_becomes_semi_join(self):
        self.assertEqual(run(DB, query("in")), {"plan": ["semi_join"], "rows": [[1], [3]]})

    def test_not_in_on_non_null_columns(self):
        self.assertEqual(run(DB, query("not_in")), {"plan": ["anti_join"], "rows": [[2]]})

    def test_no_rewrite_keeps_subplan(self):
        self.assertEqual(run(DB, query("not_in"), rewrite=False), {"plan": ["subplan"], "rows": [[2]]})


if __name__ == "__main__":
    unittest.main()
