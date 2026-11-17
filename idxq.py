#!/usr/bin/env python3
"""Plan and run one query over a small table with composite indexes."""
import json
import sys

from executor import execute, seek_keys
from planner import choose

db = json.load(open(sys.argv[1], encoding="utf-8"))
query = json.load(open(sys.argv[2], encoding="utf-8"))
plan = choose(db, query)
rows, scanned, fetches = execute(db, query, dict(plan))
start_key, end_key = seek_keys(db, query, plan)


def jsonable(key):
    return [[col, (val.name if hasattr(val, "name") else val), inc]
            for col, val, inc in key]


print(json.dumps({"plan": plan,
                  "start_key": jsonable(start_key),
                  "end_key": jsonable(end_key),
                  "rows": rows, "scanned": scanned, "fetches": fetches}))
