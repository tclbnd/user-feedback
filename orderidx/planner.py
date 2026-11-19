"""Ordered access paths: can an index deliver the rows in ORDER BY order?

If an index (read forwards or backwards) yields rows already in the
requested order, the planner uses it and skips the sort — essential for
ORDER BY ... LIMIT on big tables.  Otherwise it scans and sorts.
"""
from .sortkeys import normalize, sort_rows


def _match(index, order, prefix_cols):
    """Return "forward", "backward" or None."""
    cols = [c for c in index.columns if c["col"] not in prefix_cols]
    if len(order) > len(cols):
        return None
    forward = backward = True
    for want, have in zip(map(normalize, order), cols):
        if want["col"] != have["col"]:
            return None
        if want["dir"] != have["dir"]:
            forward = False
        if want["dir"] == have["dir"]:
            backward = False
    if forward:
        return "forward"
    if backward:
        return "backward"
    return None


def plan(query, indexes):
    order = query.get("order_by", [])
    eq = query.get("where_eq", {})
    for ix in indexes:
        lead = [c["col"] for c in ix.columns[:len(eq)]]
        if sorted(lead) != sorted(eq):
            continue
        how = _match(ix, order, set(eq))
        if how:
            return {"path": f"index scan {ix.name}" + (" backward" if how == "backward" else ""), "index": ix, "backward": how == "backward"}
    return {"path": "seq scan + sort"}


def execute(query, rows, indexes):
    p = plan(query, indexes)
    eq = query.get("where_eq", {})
    offset = query.get("offset", 0)
    if "index" in p:
        out = p["index"].scan(p["backward"], eq or None)
    else:
        out = sort_rows([r for r in rows if all(r.get(c) == v for c, v in eq.items())], query.get("order_by", []))
        out = out[offset:]
    limit = query.get("limit")
    out = out[:limit] if limit is not None else out
    return {"path": p["path"], "ids": [r["id"] for r in out]}
