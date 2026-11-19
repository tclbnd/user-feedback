"""Grouped aggregation over grouping sets.

The result has one row per (grouping set, group).  Columns not in the
row's grouping set are NULL.  ``grouping(cols)`` returns the GROUPING()
bitmask for the row.
"""
AGGS = {
    "count": lambda vs: sum(1 for v in vs if v is not None),
    "count_star": lambda vs: len(vs),
    "sum": lambda vs: sum(v for v in vs if v is not None) if any(v is not None for v in vs) else None,
    "min": lambda vs: min((v for v in vs if v is not None), default=None),
    "max": lambda vs: max((v for v in vs if v is not None), default=None),
}


def grouping(row_set, cols):
    mask = 0
    for i, c in enumerate(cols):
        if c not in row_set:
            mask |= 1 << i
    return mask


def run(rows, select, group_sets):
    """select: {"cols": [...], "aggs": [[fn, col], ...], "grouping": [[cols], ...]}"""
    out = []
    for gs in group_sets:
        groups = {}
        for r in rows:
            key = tuple(r.get(c) for c in gs)
            groups.setdefault(key, []).append(r)
        for key, members in groups.items():
            row = {c: (dict(zip(gs, key)).get(c) if c in gs else None) for c in select.get("cols", [])}
            for fn, col in select.get("aggs", []):
                vals = [m.get(col) for m in members] if col else [1] * len(members)
                row[f"{fn}({col or '*'})"] = AGGS[fn](vals)
            for cols in select.get("grouping", []):
                row[f"grouping({','.join(cols)})"] = grouping(gs, cols)
            out.append(row)
    return out
