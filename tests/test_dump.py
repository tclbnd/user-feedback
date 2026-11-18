import unittest

from dumper.dump import Dump
from dumper.mvcc import Store
from dumper.restore import check_orders
from dumper.workload import OrderWorkload


class DumpTests(unittest.TestCase):
    def test_quiet_database_dump_is_consistent(self):
        store = Store()
        OrderWorkload(store, seed=2, initial=25)
        dump = Dump(store, ["orders", "order_lines"])
        for _ in dump.run():
            pass
        self.assertEqual(check_orders(dump.data), [])
        self.assertEqual(len(dump.data["orders"]), 25)

    def test_mvcc_read_at_snapshot(self):
        store = Store()
        t1 = store.commit([("t", "a", 1)])
        store.commit([("t", "a", 2)])
        self.assertEqual(store.read("t", "a", t1), 1)
        self.assertEqual(store.keys("t", 0), [])


if __name__ == "__main__":
    unittest.main()
