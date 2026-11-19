import unittest

from plancache.catalog import Catalog
from plancache.reload import indexes_of, swap_reload
from plancache.prepared import Server
from plancache.stats import statement_view


class ReloadTests(unittest.TestCase):
    def test_swap_keeps_index_names(self):
        cat = Catalog()
        cat.create_table("customers", 10)
        cat.create_index("cust_pk", "customers", "id")
        swap_reload(cat, "customers", 500, indexes_of(cat, "customers"))
        self.assertEqual(cat.tables["customers"].rows, 500)
        self.assertEqual(cat.tables["customers"].stats_rows, 500)
        self.assertEqual(indexes_of(cat, "customers"), {"cust_pk": "id"})

    def test_statement_view(self):
        cat = Catalog()
        cat.create_table("orders", 10)
        cat.create_table("customers", 10)
        server = Server(cat)
        server.prepare("q", {"outer": "orders", "inner": "customers", "join_col": "id"})
        for _ in range(4):
            server.execute("q")
        self.assertEqual(statement_view(server)[0]["hit_ratio"], 0.75)


if __name__ == "__main__":
    unittest.main()
