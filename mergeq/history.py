"""Type 2 history of the customer dimension.

Every time the sync changes or removes a ``dim_customer`` row, the version
it replaces is closed and appended to ``dim_customer_history`` with the
``updated_at`` of the change as ``valid_to``.  Finance uses the history to
attribute revenue to the tier a customer had at the time of the order.
"""
from .table import Table

HISTORY_COLUMNS = ["version_id", "customer_id", "email", "name", "tier", "valid_from", "valid_to", "change"]


def history_table(rows=()):
    t = Table("dim_customer_history", "version_id", HISTORY_COLUMNS)
    for r in rows:
        t.insert(r)
    return t


class HistoryWriter:
    def __init__(self, table):
        self.table = table

    def __call__(self, change, old, new):
        if old is None:
            return
        self.table.insert({
            "version_id": self.table.next_rowid,
            "customer_id": old["customer_id"],
            "email": old["email"],
            "name": old["name"],
            "tier": old["tier"],
            "valid_from": old["updated_at"],
            "valid_to": new["updated_at"] if new is not None else None,
            "change": change,
        })


def tier_at(history, current, customer_id, ts):
    """Tier of ``customer_id`` at time ``ts``, from history plus the current row."""
    for row in sorted(history.rows.values(), key=lambda r: r["valid_from"]):
        if row["customer_id"] == customer_id and row["valid_from"] <= ts and (row["valid_to"] is None or ts < row["valid_to"]):
            return row["tier"]
    for row in current.rows.values():
        if row["customer_id"] == customer_id and row["updated_at"] <= ts:
            return row["tier"]
    return None
