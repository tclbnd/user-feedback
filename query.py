#!/usr/bin/env python3
"""Run a query with subquery predicates, rewritten into joins or row by row."""
import argparse
import json
import sys

import expr
from planner import plan


def correlated(pred, outer_alias):
    return any(n.get("col", "").startswith(outer_alias + ".")
               for n in (pred.get("left", {}), pred.get("right", {}), pred.get("arg", {})))


class Join:
    """Nested-loop semi/anti join of the outer rows with a decorrelated subquery."""

    def __init__(self, kind, pred, db, outer_alias):
        self.kind, self.pred = kind, pred
        sub = pred["query"]
        self.alias = sub["as"]
        local = [p for p in sub.get("where", []) if not correlated(p, outer_alias)]
        self.conds = [p for p in sub.get("where", []) if correlated(p, outer_alias)]
        self.inner = [r for r in db[sub["from"]]["rows"]
                      if expr.and3(expr.plain(p, {self.alias: r}) for p in local) is True]
        self.select = sub.get("select", [])
        self.inner_has_null = any(r.get(c.split(".", 1)[1]) is None for r in self.inner for c in self.select)

    def matches(self, env):
        out = []
        for row in self.inner:
            inner = dict(env, **{self.alias: row})
            if expr.and3(expr.plain(p, inner) for p in self.conds) is True:
                out.append(inner)
        return out

    def keep(self, env):
        rows = self.matches(env)
        if "args" not in self.pred:
            return bool(rows) if self.kind == "semi_join" else not rows
        key = tuple(expr.value(a, env) for a in self.pred["args"])
        if self.kind == "null_aware_anti_join" and (None in key or self.inner_has_null):
            return False
        found = key in {tuple(expr.value({"col": c}, r) for c in self.select) for r in rows}
        return found if self.kind == "semi_join" else not found


def run(db, query, rewrite=True):
    nodes = plan(query, db, rewrite)
    steps = []
    for kind, pred in nodes:
        if kind in ("filter", "subplan"):
            steps.append(lambda env, p=pred: expr.evaluate(p, db, env) is True)
        else:
            steps.append(Join(kind, pred, db, query["as"]).keep)
    rows = []
    for row in db[query["from"]]["rows"]:
        env = {query["as"]: row}
        if all(step(env) for step in steps):
            rows.append([expr.value({"col": c}, env) for c in query["select"]])
    return {"plan": [kind for kind, _ in nodes], "rows": rows}


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
    print(json.dumps(run(db, query, not args.no_rewrite), ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
