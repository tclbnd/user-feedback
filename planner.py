"""Join planning: builds the join tree and its estimates."""
from cost import table_of


def scan(q, table):
    filters = [f for f in q.filters if table_of(f["col"]) == table]
    rows = q.scan_rows(table, filters)
    return {"op": "scan", "table": table, "filters": filters, "rows": rows,
            "cost": q.stats[table]["rows"], "tables": frozenset([table])}


def left_join(q, left, table):
    on = q.nullable[table]
    if not {table_of(c) for p in on for c in p} - {table} <= left["tables"]:
        return None
    right = scan(q, table)
    filters = []
    rows = q.left_rows(left["rows"], right["rows"], on, filters)
    return {"op": "join", "type": "left", "left": left, "right": right, "on": [list(p) for p in on],
            "filters": filters, "rows": rows, "cost": left["cost"] + right["cost"] + rows,
            "tables": left["tables"] | {table}}


def inner_join(q, left, right):
    if left["tables"] <= set(q.nullable) and len(left["tables"]) == 1:
        return None
    if right["tables"] <= set(q.nullable) and len(right["tables"]) == 1:
        return None
    span = q.spanning(left["tables"], right["tables"])
    on = [[lc[0], rc[0]] for lc, rc in span]
    rows = q.inner_rows(left["rows"], right["rows"], left["tables"], right["tables"])
    return {"op": "join", "type": "inner" if on else "cross", "left": left, "right": right, "on": on,
            "filters": [], "rows": rows, "cost": left["cost"] + right["cost"] + rows,
            "tables": left["tables"] | right["tables"]}


def choose(q):
    """Join the tables in the order they are written (left-deep)."""
    plan = scan(q, q.order[0])
    for table in q.order[1:]:
        if table in q.nullable:
            plan = left_join(q, plan, table)
        else:
            plan = inner_join(q, plan, scan(q, table))
    return plan


def strip(plan):
    out = {k: v for k, v in plan.items() if k not in ("tables", "left", "right")}
    if plan["op"] == "join":
        out["left"], out["right"] = strip(plan["left"]), strip(plan["right"])
    return out
