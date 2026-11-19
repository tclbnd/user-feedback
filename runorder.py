"""Run ORDER BY [LIMIT] queries through the planner.

    python3 runorder.py table.json queries.json result.json

table.json: {"rows": [...], "indexes": [{"name": "ix", "columns": [{"col": "a", "dir": "desc", "nulls": "last"}]}]}
queries.json: [{"where_eq": {"a": 1}, "order_by": [{"col": "b", "dir": "desc"}], "limit": 5}, ...]

Optional: "offset": n (OFFSET n), "with_ties": true (FETCH FIRST <limit> ROWS WITH TIES).
"""
import json
import sys

from orderidx.btree import BTree
from orderidx.planner import execute


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    queries = json.load(open(sys.argv[2], encoding="utf-8"))
    rows = spec["rows"]
    indexes = [BTree(i["name"], i["columns"]).build(rows) for i in spec.get("indexes", [])]
    out = [dict(execute(q, rows, indexes), query=q) for q in queries]
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
