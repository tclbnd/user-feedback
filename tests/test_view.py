import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from view import View  # noqa: E402


def ins(table, **row):
    return {"op": "insert", "table": table, "row": row}


def rm(table, id):
    return {"op": "delete", "table": table, "id": id}


def upd(table, **row):
    return {"op": "update", "table": table, "row": row}


class ViewTests(unittest.TestCase):
    def test_inserts_aggregate_by_region(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"), ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=30, status="paid")):
            v.apply(s)
        self.assertEqual(v.rows(), [{"region": "eu", "n": 2, "total": 40, "avg": 20.0, "lo": 10, "hi": 30}])

    def test_void_orders_are_filtered(self):
        v = View()
        v.apply(ins("customers", id=1, region="eu"))
        v.apply(ins("orders", id=1, cust=1, amount=10, status="void"))
        self.assertEqual(v.rows(), [])

    def test_deleting_current_min_rechecks_that_group_only(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"), ins("customers", id=2, region="us"),
                  ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=50, status="paid"),
                  ins("orders", id=3, cust=2, amount=20, status="paid")):
            v.apply(s)
        v.apply(rm("orders", 1))  # 10 was eu's min
        self.assertEqual(v.rows(), [{"region": "eu", "n": 1, "total": 50, "avg": 50.0, "lo": 50, "hi": 50},
                                    {"region": "us", "n": 1, "total": 20, "avg": 20.0, "lo": 20, "hi": 20}])
        self.assertEqual(v.rechecked, {"eu"})

    def test_deleting_current_max_rechecks_that_group_only(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"),
                  ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=30, status="paid"),
                  ins("orders", id=3, cust=1, amount=50, status="paid")):
            v.apply(s)
        v.apply(rm("orders", 3))
        self.assertEqual(v.rows(), [{"region": "eu", "n": 2, "total": 40, "avg": 20.0, "lo": 10, "hi": 30}])
        self.assertEqual(v.rechecked, {"eu"})

    def test_deleting_interior_value_needs_no_recheck(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"),
                  ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=30, status="paid"),
                  ins("orders", id=3, cust=1, amount=50, status="paid")):
            v.apply(s)
        v.apply(rm("orders", 2))
        self.assertEqual(v.rows(), [{"region": "eu", "n": 2, "total": 60, "avg": 30.0, "lo": 10, "hi": 50}])
        self.assertEqual(v.rechecked, set())

    def test_group_disappears_when_count_reaches_zero(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"),
                  ins("orders", id=1, cust=1, amount=10, status="paid")):
            v.apply(s)
        v.apply(rm("orders", 1))
        self.assertEqual(v.rows(), [])
        self.assertEqual(v.groups, {})

    def test_order_update_is_delete_then_insert(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"), ins("customers", id=2, region="us"),
                  ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=20, status="paid")):
            v.apply(s)
        # Move the min order to the other customer/region and raise its amount.
        v.apply(upd("orders", id=1, cust=2, amount=40, status="paid"))
        self.assertEqual(v.rows(), [{"region": "eu", "n": 1, "total": 20, "avg": 20.0, "lo": 20, "hi": 20},
                                    {"region": "us", "n": 1, "total": 40, "avg": 40.0, "lo": 40, "hi": 40}])
        self.assertEqual(v.rechecked, {"eu"})

    def test_order_update_to_void_leaves_group(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"),
                  ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=30, status="paid")):
            v.apply(s)
        v.apply(upd("orders", id=2, cust=1, amount=30, status="void"))
        self.assertEqual(v.rows(), [{"region": "eu", "n": 1, "total": 10, "avg": 10.0, "lo": 10, "hi": 10}])
        self.assertEqual(v.rechecked, {"eu"})

    def test_customer_region_change_moves_its_orders(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"),
                  ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=20, status="paid")):
            v.apply(s)
        v.apply(upd("customers", id=1, region="us"))
        self.assertEqual(v.rows(), [{"region": "us", "n": 2, "total": 30, "avg": 15.0, "lo": 10, "hi": 20}])
        # eu lost its only customer, so the group simply disappeared: no recheck.
        self.assertEqual(v.rechecked, set())

    def test_customer_region_change_rechecks_the_group_it_leaves(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"), ins("customers", id=2, region="eu"),
                  ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=50, status="paid"),
                  ins("orders", id=3, cust=2, amount=30, status="paid")):
            v.apply(s)
        v.apply(upd("customers", id=1, region="us"))  # takes eu's 10 and 50 away
        self.assertEqual(v.rows(), [{"region": "eu", "n": 1, "total": 30, "avg": 30.0, "lo": 30, "hi": 30},
                                    {"region": "us", "n": 2, "total": 60, "avg": 30.0, "lo": 10, "hi": 50}])
        self.assertEqual(v.rechecked, {"eu"})

    def test_orders_before_customer_join_later(self):
        v = View()
        v.apply(ins("orders", id=1, cust=1, amount=10, status="paid"))
        self.assertEqual(v.rows(), [])
        v.apply(ins("customers", id=1, region="eu"))
        self.assertEqual(v.rows(), [{"region": "eu", "n": 1, "total": 10, "avg": 10.0, "lo": 10, "hi": 10}])
        v.apply(rm("customers", 1))
        self.assertEqual(v.rows(), [])

    def test_txn_steps_are_applied_before_output(self):
        v = View()
        v.apply(ins("customers", id=1, region="eu"))
        for s in (ins("orders", id=1, cust=1, amount=10, status="paid"),
                  rm("orders", 1),
                  ins("orders", id=2, cust=1, amount=7, status="paid")):
            v.apply(s)  # a transaction's inner steps, in order
        self.assertEqual(v.rows(), [{"region": "eu", "n": 1, "total": 7, "avg": 7.0, "lo": 7, "hi": 7}])


def full_recompute(orders, customers):
    """Reference answer: run the view query from scratch over the base tables."""
    groups = {}
    for o in orders.values():
        c = customers.get(o["cust"])
        if c is None or o["status"] == "void":
            continue
        g = groups.setdefault(c["region"], {"n": 0, "total": 0, "amounts": []})
        g["n"] += 1
        g["total"] += o["amount"]
        g["amounts"].append(o["amount"])
    return [{"region": r, "n": g["n"], "total": g["total"], "avg": g["total"] / g["n"],
             "lo": min(g["amounts"]), "hi": max(g["amounts"])}
            for r, g in sorted(groups.items())]


class IncrementalCase:
    """A base-table state plus an incrementally maintained View over it."""

    def __init__(self):
        self.orders, self.customers = {}, {}
        self.v = View()
        self.next_oid, self.next_cid = 1, 1

    def insert(self, table, row):
        (self.orders if table == "orders" else self.customers)[row["id"]] = dict(row)
        self.v.apply({"op": "insert", "table": table, "row": dict(row)})

    def delete(self, table, id):
        (self.orders if table == "orders" else self.customers).pop(id)
        self.v.apply({"op": "delete", "table": table, "id": id})

    def update(self, table, row):
        (self.orders if table == "orders" else self.customers)[row["id"]] = dict(row)
        self.v.apply({"op": "update", "table": table, "row": dict(row)})

    def check(self, case):
        case.assertEqual(self.v.rows(), full_recompute(self.orders, self.customers))


class RandomizedAcceptanceTests(unittest.TestCase):
    """Random insert/delete/update sequences, compared with a full recompute."""

    REGIONS = ("eu", "us", "ap")

    def random_row(self, rnd, cid):
        return {"id": cid, "cust": rnd.randrange(1, 4), "amount": rnd.randrange(0, 6),
                "status": rnd.choice(("paid", "paid", "paid", "void"))}

    def run_sequence(self, seed, steps=120):
        rnd = random.Random(seed)
        case = IncrementalCase()
        for _ in range(steps):
            orders, customers = case.orders, case.customers
            roll = rnd.random()
            # Keep a mix of all three ops, biased by what actually exists.
            if roll < 0.35 or not customers:
                cid = case.next_cid
                case.next_cid += 1
                case.insert("customers", {"id": cid, "region": rnd.choice(self.REGIONS)})
            elif roll < 0.60 or not orders:
                oid = case.next_oid
                case.next_oid += 1
                case.insert("orders", self.random_row(rnd, oid))
            elif roll < 0.78:
                case.delete("orders", rnd.choice(sorted(orders)))
            elif roll < 0.90:
                case.update("orders", self.random_row(rnd, rnd.choice(sorted(orders))))
            else:
                cid = rnd.choice(sorted(customers))
                case.update("customers", {"id": cid, "region": rnd.choice(self.REGIONS)})
            with self.subTest(seed=seed):
                case.check(self)
        return case

    def test_random_sequences_match_full_recompute(self):
        for seed in range(40):
            with self.subTest(seed=seed):
                case = self.run_sequence(seed)
                self.assertEqual(case.v.rechecked - set(self.REGIONS), set())

    def test_random_sequence_deletes_and_rereads_extremes(self):
        # Long single run, exercising many min/max removals.
        case = self.run_sequence(1234, steps=400)
        case.check(self)

    def test_transactional_batches_match_full_recompute(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                self._run_batches(grouped)

    def _run_batches(self, grouped):
        """Apply random steps as a transaction.

        ``grouped`` mirrors the two supported shapes: a caller batching the
        steps itself (the View.batch path) or applying them one at a time, as
        ivm.py does for a txn.  Both must agree with a full recompute.
        """
        rnd = random.Random(99)
        case = IncrementalCase()
        for _ in range(60):
            cid = case.next_cid
            case.next_cid += 1
            cid2 = case.next_cid
            case.next_cid += 1
            oids = [case.next_oid, case.next_oid + 1]
            case.next_oid += 2
            batch = [
                {"op": "insert", "table": "customers",
                 "row": {"id": cid, "region": rnd.choice(self.REGIONS)}},
                {"op": "insert", "table": "customers",
                 "row": {"id": cid2, "region": rnd.choice(self.REGIONS)}},
                {"op": "insert", "table": "orders",
                 "row": {"id": oids[0], "cust": cid, "amount": rnd.randrange(0, 5),
                         "status": rnd.choice(("paid", "paid", "void"))}},
                {"op": "insert", "table": "orders",
                 "row": {"id": oids[1], "cust": cid2, "amount": rnd.randrange(0, 5),
                         "status": "paid"}},
            ]
            if case.orders and rnd.random() < 0.6:
                victim = rnd.choice(sorted(case.orders))
                batch.append({"op": "delete", "table": "orders", "id": victim})
            if case.customers and rnd.random() < 0.4:
                victim = rnd.choice(sorted(case.customers))
                batch.append({"op": "update", "table": "customers",
                              "row": {"id": victim, "region": rnd.choice(self.REGIONS)}})
            case.v.rechecked.clear()
            # Apply the batch first, mirroring it into the reference state:
            for step in batch:
                if step["op"] == "delete":
                    del case.orders[step["id"]]
                elif step["table"] == "orders":
                    case.orders[step["row"]["id"]] = dict(step["row"])
                else:
                    case.customers[step["row"]["id"]] = dict(step["row"])
            if grouped:
                with case.v.batch():
                    for step in batch:
                        case.v.apply(step)
            else:
                for step in batch:
                    case.v.apply(step)
            with self.subTest(step=len(case.orders)):
                case.check(self)


class RecheckScopeTests(unittest.TestCase):
    """A MIN/MAX recheck must read only the group it is repairing."""

    def build(self):
        v = View()
        for s in (ins("customers", id=1, region="eu"),
                  ins("customers", id=2, region="eu"),
                  ins("customers", id=3, region="us"),
                  ins("customers", id=4, region="us"),
                  ins("orders", id=1, cust=1, amount=10, status="paid"),
                  ins("orders", id=2, cust=1, amount=20, status="paid"),
                  ins("orders", id=3, cust=2, amount=30, status="paid"),
                  ins("orders", id=4, cust=3, amount=40, status="paid"),
                  ins("orders", id=5, cust=4, amount=50, status="paid"),
                  ins("orders", id=6, cust=4, amount=60, status="paid")):
            v.apply(s)
        return v

    def test_recheck_reads_only_affected_group(self):
        v = self.build()
        read = {"customers": [], "orders": []}
        orig_customers_in, orig_orders_of = v.t.customers_in, v.t.orders_of

        def customers_in(region):
            rows = orig_customers_in(region)
            read["customers"].extend(c["id"] for c in rows)
            return rows

        def orders_of(cid):
            rows = orig_orders_of(cid)
            read["orders"].extend(o["id"] for o in rows)
            return rows

        v.t.customers_in, v.t.orders_of = customers_in, orders_of
        v.apply(rm("orders", 1))  # eu's current MIN
        v.t.customers_in, v.t.orders_of = orig_customers_in, orig_orders_of

        self.assertEqual(v.rechecked, {"eu"})
        # Only eu's customers (1, 2) and their remaining orders (2, 3) were
        # touched; us customers 3, 4 and orders 4, 5, 6 were never read.
        self.assertEqual(sorted(set(read["customers"])), [1, 2])
        self.assertEqual(sorted(set(read["orders"])), [2, 3])

    def test_recheck_does_not_touch_other_groups_view_state(self):
        v = self.build()
        before = {r: dict(g) for r, g in v.groups.items() if r == "us"}
        v.apply(rm("orders", 3))  # eu's current MAX
        after = {r: dict(g) for r, g in v.groups.items() if r == "us"}
        self.assertEqual(before, after)
        self.assertEqual(v.rechecked, {"eu"})


if __name__ == "__main__":
    unittest.main()
