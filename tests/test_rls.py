import unittest

from rls.catalog import Catalog
from rls.executor import Executor
from rls.planner import rewrite

TABLES = {"orders": [{"id": 1, "tenant": "a", "status": "open"}, {"id": 2, "tenant": "b", "status": "open"},
                     {"id": 3, "tenant": "a", "status": "paid"}]}


def run(plan, tenant):
    cat = Catalog(TABLES)
    return sorted(r["id"] for r in Executor(cat).run(rewrite(plan, cat, tenant)))


class RlsTests(unittest.TestCase):
    def test_scan_is_filtered(self):
        self.assertEqual(run({"scan": "orders"}, "a"), [1, 3])
        self.assertEqual(run({"scan": "orders"}, "b"), [2])

    def test_user_filter(self):
        self.assertEqual(run({"filter": {"cmp": ["status", "=", "open"]}, "input": {"scan": "orders"}}, "a"), [1])

    def test_leakproof_function(self):
        plan = {"filter": {"fn": "eq", "args": ["col.status", "paid"]}, "input": {"scan": "orders"}}
        self.assertEqual(run(plan, "a"), [3])


if __name__ == "__main__":
    unittest.main()
