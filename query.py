#!/usr/bin/env python3
"""Run a report query with correlated subqueries."""
import argparse
import json
import sys

from engine import Context, MultipleRows, and3
from planner import plan


def run(db, query, rewrite=True):
    ctx = Context(db)
    labels = plan(query, ctx, rewrite)
    rows = []
    for row in db[query["from"]]["rows"]:
        env = {query["as"]: row}
        if and3(ctx.truth(p, env) for p in query.get("where", [])) is True:
            rows.append([ctx.operand(node, env) for node in query["select"]])
    return {"plan": labels, "rows": rows, "subplan_runs": ctx.subplan_runs}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("query")
    parser.add_argument("--no-rewrite", action="store_true")
    args = parser.parse_args()
    with open(args.db, encoding="utf-8") as fh:
        db = json.load(fh)["tables"]
    with open(args.query, encoding="utf-8") as fh:
        query = json.load(fh)
    try:
        print(json.dumps(run(db, query, not args.no_rewrite), ensure_ascii=False))
    except MultipleRows as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
