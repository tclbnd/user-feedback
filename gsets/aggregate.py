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
    """PostgreSQL GROUPING() bitmask.

    Arguments are inspected right to left: the rightmost argument owns
    the least-significant bit (1 << 0), the one to its left the next
    bit, and so on.  A bit is 1 when the column was replaced by NULL
    because it is not part of the row's grouping set.
    """
    mask = 0
    for i, c in enumerate(reversed(cols)):
        if c not in row_set:
            mask |= 1 << i
    return mask


def run(rows, select, group_sets):
    """select: {"cols": [...], "aggs": [[fn, col], ...], "grouping": [[cols], ...]}"""
    out = []
    select_cols = select.get("cols", [])
    aggs = select.get("aggs", [])
    grouping_specs = select.get("grouping", [])
    for gs in group_sets:
        groups = {}
        for r in rows:
            key = tuple(r.get(c) for c in gs)
            groups.setdefault(key, []).append(r)
        if not groups:
            # No input rows: only the grand-total set () yields one row
            # (aggregates over zero rows); column-bearing sets yield none.
            if gs:
                continue
            groups[()] = []
        for key, members in groups.items():
            values = dict(zip(gs, key))
            row = {c: values[c] if c in gs else None for c in select_cols}
            for fn, col in aggs:
                vals = [m.get(col) for m in members] if col else [1] * len(members)
                row[f"{fn}({col or '*'})"] = AGGS[fn](vals)
            for cols in grouping_specs:
                row[f"grouping({','.join(cols)})"] = grouping(gs, cols)
            out.append(row)
    return out
