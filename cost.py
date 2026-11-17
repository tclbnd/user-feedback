"""Cost model and candidate generation (constants are documented in README)."""
import math

PROBE, LOOKUP, RANGE_SEL, WRITE = 3.0, 1.5, 0.1, 2.0


def sel(stats, table, col, kind):
    return 1.0 / stats[table]["columns"][col]["ndv"] if kind == "eq" else RANGE_SEL


def filtered(stats, q, table):
    return stats[table]["rows"] * math.prod(sel(stats, t, c, k) for t, c, k in q["preds"] if t == table)


def access(stats, q, table, index, want_order):
    """(cost, sorted?) of reading `table` through `index` (None = sequential scan)."""
    n = stats[table]["rows"]
    if index is None:
        return n, False
    preds = {c: k for t, c, k in q["preds"] if t == table}
    m, used, pos = n, 0, 0
    for pos, col in enumerate(index["columns"]):
        if preds.get(col) != "eq":
            break
        m *= sel(stats, table, col, "eq")
        used += 1
    else:
        pos = len(index["columns"])
    rest = index["columns"][pos:]
    ordered = bool(want_order) and rest[:len(want_order)] == want_order
    if rest and preds.get(rest[0]) == "range":
        m *= RANGE_SEL
        used += 1
        ordered = ordered and want_order[:1] == rest[:1]
    if not used and not ordered:
        return None
    return PROBE + LOOKUP * m, ordered


def query_cost(stats, q, indexes):
    """(cost per execution, index names used)."""
    table, on = q["table"], indexes
    order = q["order"] if q["kind"] == "select" else []
    best = None
    for ix in [None] + [i for i in on if i["table"] == table]:
        got = access(stats, q, table, ix, order)
        if got is None:
            continue
        cost = got[0] + (0 if got[1] or not order else filtered(stats, q, table))
        if best is None or cost < best[0]:
            best = (cost, [ix["name"]] if ix else [])
    cost, used = best
    if q["join"]:
        inner, col = q["join"]["table"], q["join"]["column"]
        outer_rows, n = filtered(stats, q, table), stats[inner]["rows"]
        options = [(n + outer_rows, [])]
        for ix in on:
            if ix["table"] == inner and ix["columns"][0] == col:
                ndv = stats[inner]["columns"][col]["ndv"]
                options.append((outer_rows * (PROBE + LOOKUP * n / ndv), [ix["name"]]))
        join_cost, join_used = min(options, key=lambda o: o[0])
        cost, used = cost + join_cost, used + join_used
    if q["kind"] != "select":
        rows = 1 if q["kind"] == "insert" else filtered(stats, q, table)
        if q["kind"] == "insert":
            cost = 0.0
        touched = [i for i in on if i["table"] == table and set(i["columns"]) & set(q["set"])]
        cost += WRITE * rows * len(touched)
    return cost, used


def candidates(stats, q):
    out = []
    for table in [q["table"]] + ([q["join"]["table"]] if q["join"] else []):
        eq = sorted({c for t, c, k in q["preds"] if t == table and k == "eq"},
                    key=lambda c: (-stats[table]["columns"][c]["ndv"], c))
        where = list(dict.fromkeys(c for t, c, k in q["preds"] if t == table))
        shapes = [where, eq]
        if q["join"] and table == q["join"]["table"]:
            shapes.append([q["join"]["column"]])
        for cols in shapes:
            if cols and cols not in [c["columns"] for c in out if c["table"] == table]:
                out.append({"name": f"ix_{table}_" + "_".join(cols), "table": table, "columns": cols})
    return out


def size(stats, ix):
    t = stats[ix["table"]]
    return t["rows"] * (sum(t["columns"][c]["width"] for c in ix["columns"]) + 8)
