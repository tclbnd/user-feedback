import unittest

from mergeq.dimsync import customer_table, run_sync, tier_counts


def cust(i, tier="silver", ts=10, **kw):
    return dict({"customer_id": i, "email": f"u{i}@x.com", "name": f"U{i}", "tier": tier, "active": True, "updated_at": ts}, **kw)


class SyncTests(unittest.TestCase):
    def test_new_customers_are_inserted(self):
        t = customer_table([cust(1)])
        out = run_sync(t, [dict(cust(2, "gold", 20), op="upsert"), dict(cust(3, ts=20), op="upsert")])
        self.assertTrue(out["ok"])
        self.assertEqual(tier_counts(t), {"gold": 1, "silver": 2})

    def test_delete_marker(self):
        t = customer_table([cust(1), cust(2)])
        run_sync(t, [dict(cust(2), op="delete")])
        self.assertEqual(sorted(r["customer_id"] for r in t.rows.values()), [1])

    def test_newer_change_updates(self):
        t = customer_table([cust(1)])
        run_sync(t, [dict(cust(1, "gold", 20), op="upsert")])
        self.assertEqual(tier_counts(t), {"gold": 1})


if __name__ == "__main__":
    unittest.main()
