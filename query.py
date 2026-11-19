"""Run jsonb queries through each index and as a full scan.

    python3 query.py docs.json queries.json result.json

docs.json:    {"p1": "<jsonb text>", ...}   (documents as JSON text)
queries.json: [{"op": "@>", "arg": "<jsonb text>"}, {"op": "?", "arg": "color"},
               {"op": "?|", "arg": ["a", "b"]}, {"op": "?&", "arg": ["a", "b"]}, ...]

For every query the result lists, per index operator class, the plan and
rows, and the rows of a full scan.
"""
import json
import sys

from jsonbx.stats import index_report
from jsonbx.table import DocTable
from jsonbx.value import parse


def main():
    docs = json.load(open(sys.argv[1], encoding="utf-8"))
    queries = json.load(open(sys.argv[2], encoding="utf-8"))
    table = DocTable(("jsonb_ops", "jsonb_path_ops"))
    for rid, text in docs.items():
        table.insert(rid, text)
    out = []
    for q in queries:
        arg = parse(q["arg"]) if q["op"] == "@>" else q["arg"]
        row = {"op": q["op"], "arg": q["arg"], "scan": table.scan(q["op"], arg)}
        for name in table.indexes:
            row[name] = table.query(q["op"], arg, name)
        out.append(row)
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump({"queries": out, "indexes": index_report(table)}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
