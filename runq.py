"""Run queries row by row and through the subquery-flattening planner.

    python3 runq.py db.json queries.json result.json

db.json: {"customers": [{"id": 1, "region": "eu"}, ...], "orders": [...]}
queries.json: [{"from": "customers", "where": <pred>}, ...]  (see subq/expr.py)
Correlated EXISTS subqueries list their correlation as "corr": [[outer_col, inner_col], ...]
and refer to the outer row with {"outer": col} in their WHERE.
"""
import json
import sys

from subq.expr import filter_rows
from subq.rewrite import run


def with_corr(sub):
    """Fold the "corr" list into the subquery's WHERE for row-by-row evaluation."""
    if not sub.get("corr"):
        return sub
    conds = [{"cmp": {"left": {"col": inner}, "op": "=", "right": {"outer": outer}}} for outer, inner in sub["corr"]]
    if sub.get("where") is not None:
        conds.append(sub["where"])
    return dict(sub, where={"and": conds})


def expand(p):
    if isinstance(p, dict):
        out = {}
        for k, v in p.items():
            if k in ("exists", "not_exists"):
                out[k] = dict(v, sub=with_corr(v["sub"]))
            else:
                out[k] = expand(v)
        return out
    if isinstance(p, list):
        return [expand(x) for x in p]
    return p


def main():
    db = json.load(open(sys.argv[1], encoding="utf-8"))
    queries = json.load(open(sys.argv[2], encoding="utf-8"))
    out = []
    for q in queries:
        naive = filter_rows(db, q["from"], expand(q.get("where")))
        rows, info = run(db, q)
        out.append({"query": q, "row_by_row": sorted(r["id"] for r in naive),
                    "planned": sorted(r["id"] for r in rows), "plan": info})
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
