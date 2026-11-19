"""Evaluate window function calls over a table.

A call: {"fn": name, "arg": col or null, "n": int (nth_value), "window": window}.
Rows are partitioned, ordered (NULLs sort last ascending, first descending),
and every row gets the function's value.  The result is keyed by row id.
"""
import functools

from .frames import frame_rows
from .functions import agg, ranking, value_fn

AGGS = {"sum", "avg", "count", "count_star", "min", "max"}
VALUES = {"first_value", "last_value", "nth_value"}


def _cmp(a, b, order):
    for o in order:
        x, y = a.get(o["col"]), b.get(o["col"])
        desc = o.get("dir", "asc") == "desc"
        if x is None and y is None:
            continue
        if x is None or y is None:
            return (-1 if desc else 1) if x is None else (1 if desc else -1)
        if x != y:
            c = -1 if x < y else 1
            return -c if desc else c
    return 0


def evaluate(rows, call):
    w = call["window"]
    order = w.get("order_by", [])
    parts = {}
    for r in rows:
        parts.setdefault(tuple(r.get(c) for c in w.get("partition_by", [])), []).append(r)
    out = {}
    for part in parts.values():
        part = sorted(part, key=functools.cmp_to_key(lambda a, b: _cmp(a, b, order)))
        keys = [tuple(r.get(o["col"]) for o in order) for r in part]
        for i, r in enumerate(part):
            fn = call["fn"]
            if fn in ("row_number", "rank", "dense_rank"):
                out[r["id"]] = ranking(fn, keys, i)
                continue
            idx = frame_rows(w, part, i, keys)
            vals = [part[j].get(call["arg"]) if call.get("arg") else 1 for j in idx]
            out[r["id"]] = agg(fn, vals) if fn in AGGS else value_fn(fn, vals, call.get("n"))
    return out
