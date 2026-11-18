"""Order service on top of the store."""
from .engine import UniqueViolation


class OrderError(Exception):
    def __init__(self, message, order_id=None):
        super().__init__(message)
        self.order_id = order_id


def place_order(db, customer, items, fail=False):
    """Create one order with its lines; ``fail`` simulates a business check
    failing after the order number was taken."""
    tx = db.begin()
    order_id = tx.nextval("orders_id")
    try:
        tx.insert("orders", order_id, {"customer": customer, "total": sum(i["price"] * i["qty"] for i in items)})
        for n, item in enumerate(items, 1):
            tx.insert("order_lines", f"{order_id}/{n}", dict(item, order=order_id))
        if fail:
            raise OrderError("payment declined", order_id)
    except (OrderError, UniqueViolation):
        tx.rollback()
        raise
    tx.commit()
    return order_id


def bulk_import(db, customer, orders):
    """Import several orders in one transaction (used by the nightly sync)."""
    tx = db.begin()
    ids = []
    for items in orders:
        order_id = tx.nextval("orders_id")
        tx.insert("orders", order_id, {"customer": customer, "total": sum(i["price"] * i["qty"] for i in items)})
        ids.append(order_id)
    tx.commit()
    return ids
