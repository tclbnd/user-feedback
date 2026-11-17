#!/usr/bin/env python3
"""Online schema change CLI: migrate a table while business traffic runs, show a database."""
import argparse
import json
import sys

from migrate import PHASES, Failure, Migration
from table import Database


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def migrate(args):
    db = Database(args.database, load(args.workload) if args.workload else [])
    m = Migration(db, load(args.alter), args.chunk, args.fail_at)
    try:
        m.run()
    except Failure as exc:
        m.rollback()
        db.save()
        print(json.dumps({"status": "rolled_back", "reason": str(exc), "clock": db.clock, **m.summary}))
        return 3
    db.finish()
    db.save()
    print(json.dumps({"status": "done", "lock_ticks": db.lock_ticks, "clock": db.clock, **m.summary}))
    return 0 if db.lock_ticks <= args.max_lock else 4


def show(args):
    data = load(args.database)
    print("tables:", ", ".join(f"{n}({len(t['rows'])})" for n, t in sorted(data["tables"].items())),
          "| triggers:", ", ".join(t["name"] for t in data["triggers"]) or "-")
    if args.table:
        t = data["tables"][args.table]
        print(" | ".join(t["columns"]))
        for row in t["rows"]:
            print(" | ".join(json.dumps(row[c]) for c in t["columns"]))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate")
    for flag, kw in (("database", {}), ("alter", {}), ("--workload", {}), ("--chunk", {"type": int, "default": 4}),
                     ("--max-lock", {"type": int, "default": 2}), ("--fail-at", {"choices": PHASES})):
        m.add_argument(flag, **kw)
    s = sub.add_parser("show")
    s.add_argument("database")
    s.add_argument("table", nargs="?")
    args = parser.parse_args()
    return migrate(args) if args.cmd == "migrate" else show(args)


if __name__ == "__main__":
    sys.exit(main())
