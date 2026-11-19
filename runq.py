"""Run queries row by row and through the subquery-flattening planner.

    python3 runq.py db.json queries.json result.json

db.json: {"customers": [{"id": 1, "region": "eu"}, ...], "orders": [...]}
queries.json: [{"from": "customers", "where": <pred>}, ...]  (see subq/expr.py)
Correlated subqueries list their correlation as "corr": [[outer_col, inner_col], ...];
subq/expr.py applies those "inner = outer" equalities (three-valued, like SQL) for
both evaluation paths.
"""
import json
import sys

from subq.expr import filter_rows
from subq.rewrite import run


def main():
    db = json.load(open(sys.argv[1], encoding="utf-8"))
    queries = json.load(open(sys.argv[2], encoding="utf-8"))
    out = []
    for q in queries:
        # subq.expr.evaluate applies "corr" itself, so the same predicate
        # tree feeds both the row-by-row evaluator and the flattener.
        naive = filter_rows(db, q["from"], q.get("where"))
        rows, info = run(db, q)
        out.append({"query": q, "row_by_row": sorted(r["id"] for r in naive),
                    "planned": sorted(r["id"] for r in rows), "plan": info})
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
