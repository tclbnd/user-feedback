#!/usr/bin/env python3
"""Plan and run one query over a small table with composite indexes."""
import json
import sys

from executor import execute
from planner import choose

db = json.load(open(sys.argv[1], encoding="utf-8"))
query = json.load(open(sys.argv[2], encoding="utf-8"))
plan = choose(db, query)
rows, scanned, fetches = execute(db, query, dict(plan))
print(json.dumps({"plan": plan, "rows": rows, "scanned": scanned, "fetches": fetches}))
