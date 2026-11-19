"""Ordered access paths: can an index deliver the rows in ORDER BY order?

If an index (read forwards or backwards) yields rows already in the
requested order, the planner uses it and skips the sort — essential for
ORDER BY ... LIMIT on big tables.  Otherwise it scans and sorts.
"""
from .sortkeys import compare_rows, normalize, sort_rows


def _match(index, order, prefix_cols):
    """Return "forward", "backward" or None.

    An index read forwards delivers rows in its stored order; read backwards
    both the sort direction and the NULL placement of every column are
    reversed.  So the index only satisfies the ORDER BY (and lets the sort be
    skipped) when each requested column matches the index column on *both*
    direction and NULL placement — equal for a forward scan, opposite for a
    backward scan.
    """
    cols = [c for c in index.columns if c["col"] not in prefix_cols]
    if len(order) > len(cols):
        return None
    forward = backward = True
    for want, have in zip(map(normalize, order), cols):
        have = normalize(have)
        if want["col"] != have["col"]:
            return None
        same_dir = want["dir"] == have["dir"]
        same_nulls = want["nulls"] == have["nulls"]
        if not (same_dir and same_nulls):
            forward = False
        if not (not same_dir and not same_nulls):
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
    order = query.get("order_by", [])
    if "index" in p:
        out = p["index"].scan(p["backward"], eq or None)
    else:
        out = sort_rows(
            [r for r in rows if all(r.get(c) == v for c, v in eq.items())],
            order,
        )
    # OFFSET applies after ordering, whichever path produced the rows.
    offset = query.get("offset", 0)
    out = out[offset:]
    limit = query.get("limit")
    if limit is not None:
        page = out[:limit]
        # FETCH FIRST n ROWS WITH TIES: keep every later row that compares
        # equal on all ORDER BY keys to the last returned row.
        if query.get("with_ties") and limit > 0 and page:
            last = page[-1]
            for r in out[limit:]:
                if compare_rows(r, last, order) == 0:
                    page.append(r)
                else:
                    break
        out = page
    return {"path": p["path"], "ids": [r["id"] for r in out]}
