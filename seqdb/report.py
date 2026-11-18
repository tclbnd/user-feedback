"""Daily summary used by the finance dashboard."""
from collections import defaultdict


def daily_summary(db):
    orders = db.tables.get("orders", {})
    per_customer = defaultdict(lambda: {"orders": 0, "total": 0})
    for row in orders.values():
        entry = per_customer[row["customer"]]
        entry["orders"] += 1
        entry["total"] += row["total"]
    return {
        "orders": len(orders),
        "revenue": sum(r["total"] for r in orders.values()),
        "customers": dict(sorted(per_customer.items())),
    }


def top_customers(db, n=3):
    summary = daily_summary(db)["customers"]
    ranked = sorted(summary.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
    return [name for name, _ in ranked[:n]]
