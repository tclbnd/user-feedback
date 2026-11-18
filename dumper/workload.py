"""The order service's write pattern, used to rehearse dumps.

Orders and their lines are always written in one transaction: an order's
``total`` equals the sum of its lines' ``amount``.
"""
import random


class OrderWorkload:
    def __init__(self, store, seed=1, initial=40):
        self.store = store
        self.r = random.Random(seed)
        self.next_order = 1
        self.lines = {}       # order -> [line keys]
        for _ in range(initial):
            self.new_order()

    def new_order(self):
        oid = f"o{self.next_order:04d}"
        self.next_order += 1
        writes, total, keys = [], 0, []
        for n in range(self.r.randrange(1, 4)):
            amount = self.r.randrange(1, 100)
            key = f"{oid}-{n}"
            writes.append(("order_lines", key, {"order": oid, "amount": amount}))
            keys.append(key)
            total += amount
        writes.append(("orders", oid, {"total": total}))
        self.lines[oid] = keys
        return self.store.commit(writes)

    def add_line(self):
        if not self.lines:
            return self.new_order()
        oid = self.r.choice(sorted(self.lines))
        key = f"{oid}-{len(self.lines[oid])}"
        amount = self.r.randrange(1, 100)
        total = self.store.read("orders", oid, self.store.now())["total"] + amount
        self.lines[oid].append(key)
        return self.store.commit([("order_lines", key, {"order": oid, "amount": amount}), ("orders", oid, {"total": total})])

    def cancel_order(self):
        if not self.lines:
            return self.new_order()
        oid = self.r.choice(sorted(self.lines))
        writes = [("order_lines", k, None) for k in self.lines.pop(oid)] + [("orders", oid, None)]
        return self.store.commit(writes)

    def step(self):
        x = self.r.random()
        if x < 0.5:
            return self.new_order()
        if x < 0.8:
            return self.add_line()
        return self.cancel_order()
