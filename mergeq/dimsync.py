"""Daily sync of the customer dimension from the CRM extract."""
from .audit import summarize
from .history import HistoryWriter
from .merge import merge
from .session import Session
from .table import Table

CUSTOMER_COLUMNS = ["customer_id", "email", "name", "tier", "active", "updated_at"]

DAILY_MERGE = {
    "on": ["customer_id", "customer_id"],
    "when": [
        {"kind": "matched", "cond": {"cmp": ["=", "src.op", "delete"]}, "action": "delete"},
        {"kind": "matched", "cond": {"cmp": [">", "src.updated_at", "tgt.updated_at"]}, "action": "update",
         "set": {"email": "src.email", "name": "src.name", "tier": "src.tier", "updated_at": "src.updated_at"}},
        {"kind": "not_matched", "cond": {"cmp": ["!=", "src.op", "delete"]}, "action": "insert",
         "values": {"customer_id": "src.customer_id", "email": "src.email", "name": "src.name",
                    "tier": "src.tier", "active": True, "updated_at": "src.updated_at"}},
    ],
}


def customer_table(rows=()):
    t = Table("dim_customer", "customer_id", CUSTOMER_COLUMNS, unique=("email",))
    for r in rows:
        t.insert(r)
    return t


def run_sync(table, extract, stmt=DAILY_MERGE, history=None):
    """Run ``stmt`` as one statement in one transaction.

    When ``history`` (the dim_customer_history table) is given, replaced
    versions are recorded there.
    """
    session = Session({"dim_customer": table})
    session.begin()
    errors = []
    on_change = HistoryWriter(history) if history is not None else None
    outcome = session.run(merge, table, extract, stmt, errors, on_change)
    if outcome["ok"]:
        session.commit()
    else:
        session.rollback()
    outcome["row_errors"] = errors
    outcome["audit"] = summarize(extract, outcome)
    return outcome


def tier_counts(table):
    counts = {}
    for row in table.rows.values():
        counts[row["tier"]] = counts.get(row["tier"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: str(kv[0])))
