import unittest

from mergeq.dimsync import customer_table, run_sync
from mergeq.history import history_table, tier_at
from mergeq.nightly import run_nightly


def cust(i, tier="silver", ts=10, **kw):
    return dict({"customer_id": i, "email": f"u{i}@x.com", "name": f"U{i}", "tier": tier, "active": True, "updated_at": ts}, **kw)


class HistoryTests(unittest.TestCase):
    def test_update_closes_old_version(self):
        t, h = customer_table([cust(1)]), history_table()
        out = run_sync(t, [dict(cust(1, "gold", 20), op="upsert")], history=h)
        self.assertTrue(out["audit"]["balanced"])
        self.assertEqual(tier_at(h, t, 1, 15), "silver")
        self.assertEqual(tier_at(h, t, 1, 25), "gold")

    def test_nightly_chunks(self):
        t, h = customer_table([cust(i) for i in range(1, 6)]), history_table()
        src = [dict(cust(i, "gold", 20), op="upsert") for i in range(1, 9)]
        out = run_nightly(t, src, h, chunk_rows=3)
        self.assertEqual(out["result"], {"inserted": 3, "updated": 5, "deleted": 0, "skipped": 0})
        self.assertEqual(len(h.rows), 5)


if __name__ == "__main__":
    unittest.main()
