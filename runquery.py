"""Run a query plan for one tenant.

    python3 runquery.py db.json query.json result.json

db.json: {"tables": {"orders": [{"id": 1, "tenant": "acme", ...}]}, "views": {"v": <plan>}}
query.json: {"tenant": "acme", "plan": <plan>}
       or   {"requests": [{"tenant": "acme", "plan": <plan>}, ...]}   several sessions served
            by one worker, in order (see rls/server.py)

Plans: {"scan": table}, {"view": name}, {"filter": pred, "input": plan}.
Predicates: {"cmp": [col, op, value]}, {"fn": name, "args": [...]},
{"and": [...]}, {"exists": {"plan": plan, "corr": [outer_col, inner_col]}}.
Arguments and values may be literals, "col.<name>" or "outer.<name>".
"""
import json
import sys

from rls.catalog import Catalog, QueryError
from rls.executor import Executor
from rls.planner import rewrite
from rls.server import serve


def main():
    db = json.load(open(sys.argv[1], encoding="utf-8"))
    q = json.load(open(sys.argv[2], encoding="utf-8"))
    catalog = Catalog(db["tables"], db.get("views"))
    if "requests" in q:
        out = {"results": serve(catalog, q["requests"]), "leak_log": catalog.leak_log}
        with open(sys.argv[3], "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        return
    ex = Executor(catalog)
    try:
        rows = ex.run(rewrite(q["plan"], catalog, q["tenant"]))
        out = {"ids": sorted(r["id"] for r in rows)}
    except QueryError as exc:
        out = {"error": str(exc)}
    out.update(leak_log=catalog.leak_log, rows_scanned=ex.rows_scanned, subplan_runs=ex.subplan_runs)
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
