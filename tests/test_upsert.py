import unittest

from rowexec.dml import Session
from rowexec.schema import Table

SPEC = {"name": "p", "key": "sku", "columns": [{"name": "sku"}, {"name": "price"}, {"name": "qty", "default": 1},
                                                {"name": "total", "generated": {"mul": [{"col": "price"}, {"col": "qty"}]}}]}


class UpsertTests(unittest.TestCase):
    def test_insert_path(self):
        s = Session(Table(SPEC))
        rows = s.upsert([{"sku": "a", "price": 3}], {"price": {"excluded": "price"}})
        self.assertEqual(rows[0]["total"], 3)

    def test_conflict_updates_price(self):
        s = Session(Table(SPEC))
        s.insert([{"sku": "a", "price": 3, "qty": 2}])
        rows = s.upsert([{"sku": "a", "price": 5}], {"price": {"excluded": "price"}})
        self.assertEqual((rows[0]["price"], rows[0]["total"]), (5, 10))


if __name__ == "__main__":
    unittest.main()
