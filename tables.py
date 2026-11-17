"""Base tables with an orders-by-customer index."""


class Tables:
    def __init__(self):
        self.orders, self.customers, self.by_cust = {}, {}, {}

    def put_order(self, row):
        old = self.orders.get(row["id"])
        self.orders[row["id"]] = dict(row)
        self.by_cust.setdefault(row["cust"], set()).add(row["id"])
        return old

    def drop_order(self, oid):
        old = self.orders.pop(oid)
        self.by_cust[old["cust"]].discard(oid)
        return old

    def orders_of(self, cust):
        return [self.orders[i] for i in sorted(self.by_cust.get(cust, ()))]
