#!/usr/bin/env python3
"""Replay base-table changes and print the maintained view."""
import json
import sys

from view import View

view, out = View(), []
for step in json.load(open(sys.argv[1], encoding="utf-8")):
    if step["op"] == "txn":
        for s in step["steps"]:
            view.apply(s)
    elif step["op"] == "view":
        out.append({"op": "view", "rows": view.rows()})
    elif step["op"] == "stats":
        out.append({"op": "stats", "rechecked": sorted(view.rechecked)})
        view.rechecked.clear()
    else:
        view.apply(step)
print(json.dumps(out))
