"""Replay an operations scenario against a primary/standby pair.

    python3 sim.py scenario.json result.json

The result lists every order number handed to a caller (including orders
that were rolled back), the errors seen, and how many disk syncs happened.
"""
import json
import sys

from seqdb.disk import Disk
from seqdb.engine import Database, UniqueViolation
from seqdb.orders import OrderError, bulk_import, place_order
from seqdb.replication import Standby, ship
from seqdb.report import daily_summary

ITEM = [{"sku": "A-1", "price": 5, "qty": 1}]


def run(scenario):
    cache = scenario.get("cache", 20)
    disk = Disk()
    primary = Database(disk, sequences=(("orders_id", cache),))
    standby = Standby()
    issued, errors = [], []

    def take(fn):
        try:
            value = fn()
        except (OrderError, UniqueViolation) as exc:
            errors.append(str(exc))
            if getattr(exc, "order_id", None) is not None:
                issued.append(exc.order_id)
            return
        issued.extend(value if isinstance(value, list) else [value])

    for step in scenario["steps"]:
        op = step["op"]
        if op == "order":
            for _ in range(step.get("n", 1)):
                take(lambda: place_order(primary, step.get("customer", "c1"), ITEM))
        elif op == "failed_order":
            take(lambda: place_order(primary, "c1", ITEM, fail=True))
        elif op == "bulk":
            take(lambda: bulk_import(primary, "sync", [ITEM] * step["n"]))
        elif op == "ship":
            ship(primary, standby)
        elif op == "crash":
            disk.crash()
            primary = Database(disk, sequences=(("orders_id", cache),))
        elif op == "restart":
            primary.shutdown()
            primary = Database(disk, sequences=(("orders_id", cache),))
        elif op == "failover":
            primary = standby.promote()
            disk = standby.disk
            standby = Standby()
            ship(primary, standby)
        else:
            raise ValueError(f"unknown op {op}")

    return {"issued": issued, "errors": errors, "syncs": disk.syncs,
            "summary": daily_summary(primary)}


def main():
    scenario = json.load(open(sys.argv[1], encoding="utf-8"))
    result = run(scenario)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
