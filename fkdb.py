#!/usr/bin/env python3
"""Run DELETE/UPDATE statements against tables linked by foreign keys."""
import argparse
import json

from actions import execute
from catalog import Database

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["run", "show"])
parser.add_argument("script")
args = parser.parse_args()
spec = json.load(open(args.script, encoding="utf-8"))
db = Database(spec)
results = [execute(db, stmt) for stmt in spec["statements"]]
if args.command == "run":
    print(json.dumps({"results": results, "tables": db.dump()}, indent=1))
else:
    for i, r in enumerate(results):
        body = r["error"] if not r["ok"] else {"deleted": r["deleted"], "updated": r["updated"]}
        print(f"{i:>2} {'ok   ' if r['ok'] else 'ERROR'} {json.dumps(body)}")
    for name, rows in db.dump().items():
        print(f"   {name}: {json.dumps(rows)}")
