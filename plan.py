"""Plan and run queries against a table with partial indexes.

    python3 plan.py job.json result.json

job.json: {"rows": [...], "indexes": [{"name": "ix", "column": "c", "where": <pred>}],
           "queries": [<pred>, ...],
           "prepared": [{"query": <pred with {"param": name} constants>,
                         "executions": [{name: value, ...}, ...]}]}

For every query the result shows the chosen plan, the rows returned, the
rows a full scan returns, and how many rows were read.
"""
import json
import sys

from pidx.planner import PartialIndex, execute
from pidx.prepared import PreparedQuery, bind
from pidx.predicates import truth
from pidx.sizing import estimate


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    rows = job["rows"]
    indexes = [PartialIndex(i["name"], i["column"], i["where"], rows) for i in job["indexes"]]
    out = []
    for q in job["queries"]:
        res = execute(q, rows, indexes)
        res["seq_ids"] = sorted(r["id"] for r in rows if truth(q, r) is True)
        res["query"] = q
        out.append(res)
    prepared = []
    for p in job.get("prepared", []):
        stmt = PreparedQuery(p["query"], indexes)
        runs = []
        for params in p["executions"]:
            res = stmt.execute(params, rows)
            res["seq_ids"] = sorted(r["id"] for r in rows if truth(bind(p["query"], params), r) is True)
            res["params"] = params
            runs.append(res)
        prepared.append({"query": p["query"], "executions": runs, "plannings": stmt.plannings})
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump({"queries": out, "prepared": prepared, "sizes": estimate(indexes, len(rows))},
                  f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
