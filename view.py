"""Incrementally maintained view over Tables."""
from tables import Tables


class View:
    def __init__(self):
        self.t, self.groups, self.rechecked = Tables(), {}, set()

    def joined(self, order):
        cust = self.t.customers.get(order["cust"])
        if cust is None or order["status"] == "void":
            return None
        return cust["region"]

    def add(self, region, amount):
        g = self.groups.setdefault(region, {"n": 0, "total": 0, "lo": amount, "hi": amount})
        g["n"] += 1
        g["total"] += amount
        g["lo"], g["hi"] = min(g["lo"], amount), max(g["hi"], amount)

    def remove(self, region, amount):
        g = self.groups[region]
        g["n"] -= 1
        g["total"] -= amount

    def apply(self, step):
        table, op = step["table"], step["op"]
        if table == "orders" and op == "update":
            new = step["row"]
            old = self.t.put_order(new)
            region = self.joined(old)
            if region:
                g = self.groups[region]
                g["total"] += new["amount"] - old["amount"]
                g["lo"], g["hi"] = min(g["lo"], new["amount"]), max(g["hi"], new["amount"])
            return
        if table == "orders":
            old = self.t.drop_order(step["id"]) if op == "delete" else None
            if old and self.joined(old):
                self.remove(self.joined(old), old["amount"])
            if op != "delete":
                self.t.put_order(step["row"])
                region = self.joined(step["row"])
                if region:
                    self.add(region, step["row"]["amount"])
            return
        cid = step["id"] if op == "delete" else step["row"]["id"]
        if op != "insert" and cid in self.t.customers:
            mine = [o for o in self.t.orders_of(cid) if self.joined(o)]
            region = self.t.customers.pop(cid)["region"]
            for o in mine:
                self.remove(region, o["amount"])
        if op != "delete":
            self.t.customers[cid] = dict(step["row"])
            for o in self.t.orders_of(cid):
                if self.joined(o):
                    self.add(step["row"]["region"], o["amount"])

    def rows(self):
        return [{"region": r, "n": g["n"], "total": g["total"], "avg": g["total"] / g["n"] if g["n"] else None,
                 "lo": g["lo"], "hi": g["hi"]} for r, g in sorted(self.groups.items())]
