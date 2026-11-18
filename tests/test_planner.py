import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cost import Query  # noqa: E402
from joinplan import execute  # noqa: E402
from planner import choose, strip  # noqa: E402

Q = {"tables": {"a": {"rows": 100, "ndv": {"id": 100}}, "b": {"rows": 400, "ndv": {"aid": 100, "v": 4}}},
     "from": [{"table": "a"}, {"table": "b", "on": [["a.id", "b.aid"]]}],
     "where": [{"col": "b.v", "op": "=", "value": 1}],
     "data": {"a": [{"id": 1}, {"id": 2}], "b": [{"aid": 1, "v": 1}, {"aid": 2, "v": 3}, {"aid": 1, "v": 1}]}}


class PlannerTests(unittest.TestCase):
    def test_scan_estimate(self):
        plan = choose(Query(Q))
        scans = {n["table"]: n for n in (plan["left"], plan["right"])}
        self.assertAlmostEqual(scans["b"]["rows"], 100.0)
        self.assertEqual(scans["a"]["cost"], 100)

    def test_execute_two_tables(self):
        q = Query(Q)
        rows = execute(strip(choose(q)), q, Q["data"])
        self.assertEqual(sorted(r["a.id"] for r in rows), [1, 1])


if __name__ == "__main__":
    unittest.main()
