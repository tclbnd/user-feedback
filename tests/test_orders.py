import unittest

from seqdb.disk import Disk
from seqdb.engine import Database, UniqueViolation
from seqdb.orders import OrderError, bulk_import, place_order
from seqdb.replication import Standby, ship
from seqdb.report import daily_summary, top_customers
from seqdb.sequence import Sequence

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


class SequenceSafetyTests(unittest.TestCase):
    def test_rolled_back_number_is_never_reissued(self):
        db = Database(Disk())
        first = place_order(db, "alice", ITEM)
        with self.assertRaises(OrderError) as ctx:
            place_order(db, "alice", ITEM, fail=True)
        rolled_back = ctx.exception.order_id
        next_id = place_order(db, "alice", ITEM)
        self.assertGreater(rolled_back, first)
        self.assertGreater(next_id, rolled_back)
        self.assertNotIn(rolled_back, db.tables["orders"])
        self.assertEqual(len(db.tables["orders"]), 2)

    def test_crash_never_reissues_a_number(self):
        disk = Disk()
        db = Database(disk)
        ids = [place_order(db, "c", ITEM) for _ in range(7)]
        disk.crash()
        db = Database(disk)
        after = [place_order(db, "c", ITEM) for _ in range(5)]
        self.assertFalse(set(ids) & set(after))
        self.assertEqual(len(set(ids + after)), len(ids + after))

    def test_reservation_of_uncommitted_number_survives_crash(self):
        # Take a number inside a transaction that never commits, then crash.
        # The reservation was fsynced before the number was issued, so even
        # this number may not come up again after recovery.
        disk = Disk()
        db = Database(disk)
        tx = db.begin()
        leaked = tx.nextval("orders_id")
        tx.rollback()
        disk.crash()
        db = Database(disk)
        ids = [place_order(db, "c", ITEM) for _ in range(25)]
        self.assertNotIn(leaked, ids)
        self.assertEqual(len(set(ids)), 25)

    def test_failover_never_reissues_a_number(self):
        disk = Disk()
        primary = Database(disk)
        standby = Standby()
        ids = [place_order(primary, "c", ITEM) for _ in range(8)]
        ship(primary, standby)
        promoted = standby.promote()
        after = [place_order(promoted, "c", ITEM) for _ in range(25)]
        self.assertFalse(set(ids) & set(after))
        self.assertEqual(len(set(ids + after)), len(ids + after))

    def test_replicated_reservation_blocks_reuse_after_second_failover(self):
        # Reservations made on node 2 must flow to the next standby too.
        disk = Disk()
        primary = Database(disk)
        standby = Standby()
        place_order(primary, "c", ITEM)
        ship(primary, standby)
        primary = standby.promote()
        standby2 = Standby()
        ship(primary, standby2)
        more = [place_order(primary, "c", ITEM) for _ in range(3)]
        ship(primary, standby2)
        promoted = standby2.promote()
        after = [place_order(promoted, "c", ITEM) for _ in range(25)]
        self.assertFalse(set(more) & set(after))

    def test_restart_preserves_high_water_mark(self):
        disk = Disk()
        db = Database(disk)
        ids = [place_order(db, "c", ITEM) for _ in range(25)]
        db.shutdown()
        db = Database(disk)
        after = place_order(db, "c", ITEM)
        self.assertGreater(after, max(ids))


class SequencePerformanceTests(unittest.TestCase):
    def test_syncs_are_one_per_block_not_one_per_number(self):
        # End-to-end: a block boundary adds exactly one reservation sync;
        # per-number nextval adds none.  Order commits have their own sync,
        # so measure the delta around one crossing of a 20-number block.
        disk = Disk()
        db = Database(disk, sequences=(("orders_id", 20),))
        for _ in range(19):
            place_order(db, "c", ITEM)
        at = disk.syncs
        place_order(db, "c", ITEM)  # 20th, still inside the first block
        self.assertEqual(disk.syncs - at, 1)  # commit sync only
        place_order(db, "c", ITEM)  # 21st, forces one new reservation
        self.assertEqual(disk.syncs - at, 3)  # commit + reservation syncs

    def test_nextval_within_block_does_not_persist(self):
        calls = []
        seq = Sequence("s", cache=5, persist=lambda name, r: calls.append(r))
        self.assertEqual([seq.nextval() for _ in range(5)], [1, 2, 3, 4, 5])
        self.assertEqual(calls, [5])
        self.assertEqual(seq.nextval(), 6)
        self.assertEqual(calls, [5, 10])

    def test_replay_is_idempotent_and_monotone(self):
        seq = Sequence("s", cache=5)
        seq.replay_reserve(20)
        seq.replay_reserve(20)
        seq.replay_reserve(10)  # stale record must not move the mark back
        self.assertEqual(seq.snapshot(), 20)
        self.assertEqual(seq.nextval(), 21)


if __name__ == "__main__":
    unittest.main()
