"""Choose an access path for a query."""


def plan_for(index, query):
    where, cols = query.get("where", []), index["columns"]
    eq, rng, lo, hi, pos = [], None, None, None, 0
    by_col = {}
    for col, op, val in where:
        by_col.setdefault(col, []).append((op, val))
    if any(op == "=" for op, _ in by_col.get(cols[0], [])):
        eq.append(cols[0])
        pos = 1
    if pos == 0 and by_col.get(cols[pos]):
        rng = cols[pos]
        for op, val in by_col[rng]:
            if op in (">", ">="):
                lo = [val, op == ">="]
            elif op in ("<", "<="):
                hi = [val, op == "<="]
    used = set(eq) | ({rng} if rng else set())
    index_filter = []
    table_filter = [c for c in where if c[0] not in used]
    order = query.get("order_by", [])
    dirs = {d for _, d in order}
    order_ok = (dirs <= {"asc"} and len(order) <= len(cols)
                and [c for c, _ in order] == cols[:len(order)])
    needed = set(query.get("select", [])) | {c[0] for c in where} | {c for c, _ in query.get("order_by", [])}
    return {"index": index["name"], "eq": eq, "range": rng, "lo": lo, "hi": hi,
            "index_filter": index_filter, "table_filter": table_filter,
            "covering": needed <= set(cols) | {"id"},
            "order_from_index": order_ok, "direction": "desc" if order_ok and dirs == {"desc"} else "asc",
            "sort": not order_ok}


def choose(db, query):
    best, best_key = None, None
    for i, index in enumerate(db["indexes"]):
        p = plan_for(index, query)
        if not p["eq"] and not p["range"] and not (query.get("order_by") and p["order_from_index"]):
            continue
        key = (len(p["eq"]), p["range"] is not None, p["covering"], -i)
        if best_key is None or key > best_key:
            best, best_key = p, key
    if best is None:
        where = query.get("where", [])
        best = {"index": None, "eq": [], "range": None, "lo": None, "hi": None,
                "index_filter": [], "table_filter": where, "covering": False,
                "order_from_index": False, "direction": "asc", "sort": bool(query.get("order_by"))}
    return best
