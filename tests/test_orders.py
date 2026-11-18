import unittest

from seqdb.disk import Disk
from seqdb.engine import Database
from seqdb.orders import bulk_import, place_order
from seqdb.report import daily_summary, top_customers

ITEM = [{"sku": "A-1", "price": 5, "qty": 2}]


class OrderTests(unittest.TestCase):
    def test_orders_get_increasing_numbers(self):
        db = Database(Disk())
        ids = [place_order(db, "alice", ITEM) for _ in range(5)]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(daily_summary(db)["orders"], 5)

    def test_committed_orders_survive_restart(self):
        disk = Disk()
        db = Database(disk)
        place_order(db, "alice", ITEM)
        place_order(db, "bob", ITEM + ITEM)
        db.shutdown()
        db = Database(disk)
        self.assertEqual(daily_summary(db)["revenue"], 30)
        self.assertEqual(top_customers(db, 1), ["bob"])

    def test_bulk_import_uses_one_commit(self):
        disk = Disk()
        db = Database(disk)
        ids = bulk_import(db, "sync", [ITEM] * 30)
        self.assertEqual(len(set(ids)), 30)
        self.assertLessEqual(disk.syncs, 3)


if __name__ == "__main__":
    unittest.main()
