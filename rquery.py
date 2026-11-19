"""Run a recursive query.

    python3 rquery.py graph.json query.json result.json

query.json:

    {"anchor": {"id": "ceo"}, "union": "all" | "distinct",
     "columns": ["id", "depth", "path"], "cycle": ["id"] | null,
     "edge_filter": {"kind": "reports_to"}}

The result is {"rows": [...]} or {"error": "..."}.
"""
import json
import sys

from rcte.graph import load
from rcte.recursive import RowLimitExceeded, evaluate


def main():
    graph = load(json.load(open(sys.argv[1], encoding="utf-8")))
    query = json.load(open(sys.argv[2], encoding="utf-8"))
    try:
        out = {"rows": evaluate(graph, query)}
    except RowLimitExceeded as exc:
        out = {"error": str(exc)}
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    stats = graph.stats.last()
    if stats:
        print(f"iterations={stats['iterations']} rows={stats['rows']} ms={stats['ms']}", file=sys.stderr)
    sys.exit(1 if "error" in out else 0)


if __name__ == "__main__":
    main()
