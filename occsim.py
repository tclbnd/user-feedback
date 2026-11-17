#!/usr/bin/env python3
"""Run an interleaved schedule of optimistic transactions."""
import json
import sys

from kv import Store
from occ import Engine

engine, sessions, out = Engine(), {}, []
for step in json.load(open(sys.argv[1], encoding="utf-8")):
    op, s = step["op"], step.get("s")
    if op == "begin":
        sessions[s] = engine.begin()
    elif op == "get":
        out.append({"op": op, "s": s, "key": step["key"], "value": sessions[s].get(step["key"])})
    elif op == "scan":
        out.append({"op": op, "s": s, "items": [list(i) for i in sessions[s].scan(step["lo"], step["hi"])]})
    elif op == "put":
        sessions[s].put(step["key"], step["value"])
    elif op == "delete":
        sessions[s].delete(step["key"])
    elif op == "commit":
        out.append({"op": op, "s": s, "result": engine.commit(sessions.pop(s))})
    elif op == "dump":
        out.append({"op": op, "items": [list(i) for i in Store.scan(engine.store.data, "", "￿")]})
    else:
        raise SystemExit(f"unknown op {op}")
print(json.dumps(out))
