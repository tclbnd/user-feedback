import unittest

from dumper.dump import Dump
from dumper.mvcc import Store
from dumper.restore import check_orders
from dumper.workload import OrderWorkload
from rehearse import run


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

    def test_concurrent_dump_is_consistent_and_not_blocking(self):
        for seed in (1, 2, 3, 7):
            for chunk in (1, 3, 10, 25):
                with self.subTest(seed=seed, chunk=chunk):
                    cfg = {"seed": seed, "initial": 60, "writes_per_chunk": 3,
                           "chunk": chunk, "lock_tables": False, "vacuum_every": 2,
                           "tables": ["orders", "order_lines"]}
                    result = run(cfg)
                    self.assertEqual(result["problems"], [])
                    self.assertEqual(result["blocked"], 0)
                    self.assertEqual(result["snapshots_held"], [])

    def test_dump_equals_database_at_one_point_in_time(self):
        store = Store()
        work = OrderWorkload(store, seed=4, initial=20)
        dump = Dump(store, ["orders", "order_lines"], chunk=4)
        gen = dump.run()
        for step, _ in enumerate(gen):
            work.step()
            if step % 3 == 0:
                store.vacuum()
        # Rebuild the database state at the snapshot from the commit log.
        expected = {"orders": {}, "order_lines": {}}
        for entry in store.log:
            if entry["ts"] > dump.snapshot:
                break
            for table, key, value in entry["writes"]:
                if value is None:
                    expected[table].pop(key, None)
                else:
                    expected[table][key] = value
        self.assertEqual(dump.data, expected)
        self.assertEqual(store.active_snapshots, {})

    def test_snapshot_released_when_dump_is_aborted(self):
        store = Store()
        OrderWorkload(store, seed=3, initial=20)
        dump = Dump(store, ["orders", "order_lines"], chunk=5)
        gen = dump.run()
        next(gen)
        self.assertEqual(len(store.active_snapshots), 1)
        gen.close()
        self.assertEqual(store.active_snapshots, {})
        self.assertFalse(store.locked)


if __name__ == "__main__":
    unittest.main()
