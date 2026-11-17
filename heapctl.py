#!/usr/bin/env python3
"""Run a script of row operations against a heap file."""
import json
import sys

from heap import Heap

heap = Heap(sys.argv[1])
out = []
for step in json.load(open(sys.argv[2], encoding="utf-8")):
    op = step["op"]
    if op == "insert":
        out.append({"op": op, "rid": heap.insert(step["value"])})
    elif op == "update":
        heap.update(step["rid"], step["value"])
    elif op == "delete":
        heap.delete(step["rid"])
    elif op == "read":
        out.append({"op": op, "rid": step["rid"], "value": heap.read(step["rid"])})
    elif op == "stats":
        out.append({"op": op, "pages": heap.stats()})
    else:
        raise SystemExit(f"unknown op {op}")
heap.save()
print(json.dumps(out))
