"""Run a grouped query.

    python3 rungroup.py table.json query.json result.json

query.json: {"group_by": ["region", {"rollup": ["year", "quarter"]}], "distinct": false,
             "select": {"cols": ["region", "year", "quarter"], "aggs": [["sum", "amount"], ["count_star", null]],
                        "grouping": [["year", "quarter"]]}}
"""
import json
import sys

from gsets.aggregate import run
from gsets.clauses import expand


def key(row):
    return json.dumps(row, sort_keys=True)


def main():
    rows = json.load(open(sys.argv[1], encoding="utf-8"))["rows"]
    q = json.load(open(sys.argv[2], encoding="utf-8"))
    sets = expand(q["group_by"], q.get("distinct", False))
    out = sorted(run(rows, q["select"], sets), key=key)
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump({"grouping_sets": sets, "rows": out}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
