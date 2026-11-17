"""Base tables with orders-by-customer and customers-by-region indexes."""


class Tables:
    def __init__(self):
        self.orders, self.customers, self.by_cust = {}, {}, {}
        self.by_region = {}

    def put_order(self, row):
        """Insert or replace an order, keeping the by-customer index in sync."""
        oid = row["id"]
        old = self.orders.get(oid)
        if old is not None and old["cust"] != row["cust"]:
            self._unindex(oid, old["cust"])
        self.orders[oid] = dict(row)
        self.by_cust.setdefault(row["cust"], set()).add(oid)
        return old

    def drop_order(self, oid):
        old = self.orders.pop(oid)
        self._unindex(oid, old["cust"])
        return old

    def _unindex(self, oid, cust):
        ids = self.by_cust.get(cust)
        if ids is not None:
            ids.discard(oid)
            if not ids:
                del self.by_cust[cust]

    def orders_of(self, cust):
        return [self.orders[i] for i in sorted(self.by_cust.get(cust, ()))]

    def put_customer(self, row):
        """Insert or replace a customer, keeping the by-region index in sync."""
        cid = row["id"]
        old = self.customers.get(cid)
        if old is not None and old["region"] != row["region"]:
            self._unindex_region(cid, old["region"])
        self.customers[cid] = dict(row)
        self.by_region.setdefault(row["region"], set()).add(cid)
        return old

    def drop_customer(self, cid):
        old = self.customers.pop(cid)
        self._unindex_region(cid, old["region"])
        return old

    def _unindex_region(self, cid, region):
        ids = self.by_region.get(region)
        if ids is not None:
            ids.discard(cid)
            if not ids:
                del self.by_region[region]

    def customers_in(self, region):
        """Customers of one region only: the rows a single group can read."""
        return [self.customers[i] for i in sorted(self.by_region.get(region, ()))]
