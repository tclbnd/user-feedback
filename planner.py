"""Choose an access path for a query."""


def plan_for(index, query):
    where, cols = query.get("where", []), index["columns"]
    by_col = {}
    for col, op, val in where:
        by_col.setdefault(col, []).append((op, val))

    # Longest usable prefix: '=' columns extend it one by one; the first
    # column without an '=' stops the prefix. Range conditions on that
    # stopping column make it the range column; conditions on any column
    # after it can only be applied as index-internal filtering.
    eq, consumed = [], set()
    pos = 0
    while pos < len(cols):
        col = cols[pos]
        eq_cond = next((c for c in by_col.get(col, []) if c[0] == "="), None)
        if eq_cond is None:
            break
        eq.append(col)
        consumed.add((col, "=", eq_cond[1]))
        pos += 1

    rng, lo, hi = None, None, None
    if pos < len(cols) and by_col.get(cols[pos]):
        range_conds = [c for c in by_col[cols[pos]] if c[0] != "="]
        if range_conds:
            rng = cols[pos]
            for op, val in range_conds:
                consumed.add((rng, op, val))
                if op in (">", ">="):
                    # Tightest lower bound: largest value; on a tie the
                    # exclusive bound wins.
                    cand = [val, op == ">="]
                    if lo is None or (cand[0], not cand[1]) > (lo[0], not lo[1]):
                        lo = cand
                elif op in ("<", "<="):
                    # Tightest upper bound: smallest value; on a tie the
                    # exclusive bound wins.
                    cand = [val, op == "<="]
                    if hi is None or (cand[0], cand[1]) < (hi[0], hi[1]):
                        hi = cand

    index_cols = set(cols)
    index_filter, table_filter = [], []
    for cond in where:
        if tuple(cond) in consumed:
            continue
        (index_filter if cond[0] in index_cols else table_filter).append(cond)
    # ORDER BY: columns fixed by '=' carry no ordering requirement, so drop
    # them first. The remaining columns must follow the index columns right
    # after the equality prefix and share one direction; a shared descending
    # direction means a backwards scan. The trailing id key is only stored
    # ascending, so an id DESC request can never be served by index order.
    order = query.get("order_by", [])
    eff_order = [(c, d) for c, d in order if c not in eq]
    tail = cols[len(eq):] + ["id"]
    dirs = {d for _, d in eff_order}
    id_desc = any(c == "id" and d == "desc" for c, d in eff_order)
    order_ok = (not id_desc and len(dirs) == 1
                and [c for c, _ in eff_order] == tail[:len(eff_order)])
    if not eff_order:
        # Every ordering column is pinned by '=': the index order
        # trivially satisfies the ORDER BY.
        order_ok = True

    needed = (set(query.get("select") or [])
              | {c[0] for c in where}
              | {c for c, _ in order})
    covering = needed <= index_cols | {"id"}

    return {"index": index["name"], "eq": eq, "range": rng, "lo": lo, "hi": hi,
            "index_filter": index_filter, "table_filter": table_filter,
            "covering": covering, "order_from_index": order_ok,
            "direction": "desc" if order_ok and "desc" in dirs else "asc",
            "order_prefix_len": len(eq) + len(eff_order),
            "sort": not order_ok}


def choose(db, query):
    best, best_key = None, None
    delivers_order = bool(query.get("order_by"))
    for i, index in enumerate(db["indexes"]):
        p = plan_for(index, query)
        if (not p["eq"] and not p["range"]
                and not (delivers_order and p["order_from_index"])):
            continue
        key = (len(p["eq"]), p["range"] is not None, p["covering"], -i)
        if best_key is None or key > best_key:
            best, best_key = p, key
    if best is None:
        where = query.get("where", [])
        best = {"index": None, "eq": [], "range": None, "lo": None, "hi": None,
                "index_filter": [], "table_filter": where, "covering": False,
                "order_from_index": False, "direction": "asc",
                "order_prefix_len": 0,
                "sort": bool(query.get("order_by"))}
    return best
