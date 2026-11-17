"""Incrementally maintained view over Tables.

Every base change is turned into deltas of joined rows: an insert adds the rows
it creates, a delete removes them, and an update is a delete of the old row
followed by an insert of the new one.  COUNT and SUM are adjusted by the delta
(so AVG is ``total / n``); a row that lowers/raises the running MIN/MAX is
folded in directly.  When a removed row equals the group's current MIN or MAX,
only that group is rechecked from the base tables.  A group whose count drops
to zero is deleted.
"""
import contextlib

from tables import Tables


class View:
    def __init__(self):
        self.t, self.groups, self.rechecked = Tables(), {}, set()
        self._defer, self._pending = 0, set()

    def joined(self, order):
        """Region an order contributes to, or None if filtered/unjoined."""
        cust = self.t.customers.get(order["cust"])
        if cust is None or order["status"] == "void":
            return None
        return cust["region"]

    # -- group deltas ----------------------------------------------------

    def add(self, region, amount):
        g = self.groups.get(region)
        if g is None:
            self.groups[region] = {"n": 1, "total": amount, "lo": amount, "hi": amount}
            return
        g["n"] += 1
        g["total"] += amount
        if amount < g["lo"]:
            g["lo"] = amount
        if amount > g["hi"]:
            g["hi"] = amount

    def remove(self, region, amount):
        g = self.groups.get(region)
        if g is None:  # every joined row has a group, so this is an accounting bug
            raise KeyError("no group %r for a removed joined row" % (region,))
        was_lo, was_hi = amount == g["lo"], amount == g["hi"]
        g["n"] -= 1
        g["total"] -= amount
        if g["n"] == 0:
            del self.groups[region]
        elif was_lo or was_hi:
            # The removed value was an extreme: only this group can be wrong.
            self.request_recheck(region)

    def request_recheck(self, region):
        """Schedule a group recheck; batched while a multi-order change runs."""
        if self._defer:
            self._pending.add(region)
        else:
            self.recheck(region)

    @contextlib.contextmanager
    def batch(self):
        """Apply several deltas, then recheck each affected group at most once.

        Needed when one base change touches many rows of the same group (e.g. a
        customer moving region): an intermediate recheck would see a base table
        that is only half updated.
        """
        self._defer += 1
        try:
            yield
        finally:
            self._defer -= 1
            if not self._defer:
                pending, self._pending = self._pending, set()
                for region in sorted(pending):
                    self.recheck(region)

    def recheck(self, region):
        """Recompute MIN/MAX of one group from the base tables.

        Only the affected group is read: its own customers (via the region
        index) and their non-void orders.  No other group and no view state is
        consulted, so an unrelated group can never influence the answer.
        """
        g = self.groups.get(region)
        if g is None:  # the group vanished, so there was nothing to repair
            return
        self.rechecked.add(region)
        amounts = [o["amount"]
                   for c in self.t.customers_in(region)
                   for o in self.t.orders_of(c["id"]) if o["status"] != "void"]
        if not amounts:  # unreachable while n > 0 and the deltas are consistent
            raise AssertionError("group %r has n=%d but no joined rows" % (region, g["n"]))
        g["lo"], g["hi"] = min(amounts), max(amounts)

    # -- base-table operations, expressed as deltas ----------------------

    def drop_order(self, oid, missing_ok=False):
        old = self.t.orders.get(oid)
        if old is None:
            if missing_ok:
                return
            raise KeyError(oid)
        region = self.joined(old)
        # Remove from the base table first so a recheck never sees the gone row.
        self.t.drop_order(oid)
        if region is not None:
            self.remove(region, old["amount"])

    def put_order(self, row):
        if row["id"] in self.t.orders:
            self.drop_order(row["id"])
        self.t.put_order(row)
        region = self.joined(row)
        if region is not None:
            self.add(region, row["amount"])

    def drop_customer(self, cid):
        old = self.t.customers.get(cid)
        if old is None:
            return
        # The customer's orders stay in the base table but stop joining, so the
        # delta is "remove every joined order of this customer from its region".
        with self.batch():
            self.t.drop_customer(cid)
            for order in self.t.orders_of(cid):
                if order["status"] != "void":
                    self.remove(old["region"], order["amount"])

    def put_customer(self, row):
        cid = row["id"]
        with self.batch():
            if cid in self.t.customers:
                self.drop_customer(cid)
            self.t.put_customer(row)
            for order in self.t.orders_of(cid):
                if order["status"] != "void":
                    self.add(row["region"], order["amount"])

    def apply(self, step):
        table, op = step["table"], step["op"]
        if table == "orders":
            if op == "insert":
                self.put_order(dict(step["row"]))
            elif op == "delete":
                self.drop_order(step["id"])
            elif op == "update":
                new = dict(step["row"])
                self.drop_order(new["id"], missing_ok=True)
                self.put_order(new)
            else:
                raise ValueError("unknown orders op: %r" % (op,))
        elif table == "customers":
            if op in ("insert", "update"):
                self.put_customer(dict(step["row"]))
            elif op == "delete":
                self.drop_customer(step["id"])
            else:
                raise ValueError("unknown customers op: %r" % (op,))
        else:
            raise ValueError("unknown table: %r" % (table,))

    def rows(self):
        return [{"region": r, "n": g["n"], "total": g["total"],
                 "avg": g["total"] / g["n"] if g["n"] else None,
                 "lo": g["lo"], "hi": g["hi"]}
                for r, g in sorted(self.groups.items())]
