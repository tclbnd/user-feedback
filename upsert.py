#!/usr/bin/env python3
"""Run a statement script against an in-memory table."""
import argparse
import json

from executor import Session

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["run", "show"])
parser.add_argument("script")
args = parser.parse_args()
script = json.load(open(args.script, encoding="utf-8"))
session = Session(script["table"], script.get("rows", []))
results = [session.execute(s) for s in script["statements"]]
if session.in_tx:
    results.append(session.execute({"op": "rollback"}))
table = [r for _, r in sorted(session.rows.items())]
if args.command == "run":
    print(json.dumps({"results": results, "table": table}, indent=1))
else:
    for i, r in enumerate(results):
        body = {k: v for k, v in r.items() if k != "ok"}
        print(f"{i:>2} {'ok   ' if r['ok'] else 'ERROR'} {json.dumps(body.get('error', body))}")
    for row in table:
        print("   ", json.dumps(row))
