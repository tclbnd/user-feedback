import unittest

from plancache.catalog import Catalog
from plancache.planner import plan
from plancache.prepared import Server

Q = {"outer": "orders", "inner": "customers", "join_col": "id", "filter_col": "status"}


class PlannerTests(unittest.TestCase):
    def catalog(self, o, c):
        cat = Catalog()
        cat.create_table("orders", o)
        cat.create_table("customers", c)
        return cat

    def test_small_tables_use_nested_loop(self):
        self.assertEqual(plan(Q, self.catalog(10, 10)).join, "nested loop")

    def test_large_tables_use_hash_join(self):
        self.assertEqual(plan(Q, self.catalog(5000, 3000)).join, "hash join")

    def test_index_is_used(self):
        cat = self.catalog(5000, 3000)
        cat.create_index("cust_pk", "customers", "id")
        self.assertEqual(plan(Q, cat).inner_index, "cust_pk")

    def test_plan_is_reused(self):
        server = Server(self.catalog(10, 10))
        server.prepare("q", Q)
        for _ in range(20):
            server.execute("q")
        self.assertEqual(server.plannings, 1)


if __name__ == "__main__":
    unittest.main()
