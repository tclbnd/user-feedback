"""Join planning: cost-based join order by dynamic programming over subsets."""
from itertools import combinations

from cost import table_of


def scan(q, table):
    # Filters on a left-joined table are applied on its left join node,
    # so its scan carries no filters.
    filters = [] if table in q.nullable else [f for f in q.filters if table_of(f["col"]) == table]
    rows = q.scan_rows(table, filters)
    return {"op": "scan", "table": table, "filters": filters, "rows": rows,
            "cost": q.stats[table]["rows"], "tables": frozenset([table])}


def left_join(q, left, table):
    on = q.nullable[table]
    if not {table_of(c) for p in on for c in p} - {table} <= left["tables"]:
        return None
    right = scan(q, table)
    filters = [f for f in q.filters if table_of(f["col"]) == table]
    rows = q.left_rows(left["rows"], right["rows"], on, filters)
    return {"op": "join", "type": "left", "left": left, "right": right, "on": [list(p) for p in on],
            "filters": filters, "rows": rows, "cost": left["cost"] + right["cost"] + rows,
            "tables": left["tables"] | {table}}


def inner_join(q, left, right):
    if len(left["tables"]) == 1 and left["tables"] <= set(q.nullable):
        return None
    if len(right["tables"]) == 1 and right["tables"] <= set(q.nullable):
        return None
    span = q.spanning(left["tables"], right["tables"])
    on = [[lc[0], rc[0]] for lc, rc in span]
    rows = q.inner_rows(left["rows"], right["rows"], left["tables"], right["tables"])
    return {"op": "join", "type": "inner" if on else "cross", "left": left, "right": right, "on": on,
            "filters": [], "rows": rows, "cost": left["cost"] + right["cost"] + rows,
            "tables": left["tables"] | right["tables"]}


def connected(q):
    """True when the query's join graph links every table."""
    parent = {t: t for t in q.order}

    def find(t):
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    for cls in q.classes:
        tabs = sorted({table_of(c) for c in cls})
        for t in tabs[1:]:
            parent[find(tabs[0])] = find(t)
    for table, on in q.nullable.items():
        for pair in on:
            for col in pair:
                parent[find(table)] = find(table_of(col))
    return len({find(t) for t in q.order}) == 1


def splits(q, tables):
    """Candidate (left, right) table-set splits of a subset."""
    if q.shape == "left_deep":
        for t in sorted(tables):
            yield tables - {t}, frozenset([t])
    else:
        tabs = sorted(tables)
        for size in range(1, len(tabs)):
            for sub in combinations(tabs, size):
                left = frozenset(sub)
                yield left, tables - left


def choose(q):
    """Pick the cheapest plan by dynamic programming over table subsets."""
    nullable = set(q.nullable)
    best = {}
    for table in q.order:
        if table not in nullable:
            best[frozenset([table])] = scan(q, table)
    allow_cross = not connected(q)
    for size in range(2, len(q.order) + 1):
        for combo in combinations(q.order, size):
            subset = frozenset(combo)
            plans = []
            # A left-joined table enters only as the right input of its own
            # left join, as a single table, once its on-tables are on the left.
            for table in sorted(subset & nullable):
                left = best.get(subset - {table})
                if left is not None:
                    plan = left_join(q, left, table)
                    if plan is not None:
                        plans.append(plan)
            for left_set, right_set in splits(q, subset):
                left, right = best.get(left_set), best.get(right_set)
                if left is None or right is None:
                    continue
                if not allow_cross and not q.spanning(left_set, right_set):
                    continue
                plans.append(inner_join(q, left, right))
            if plans:
                best[subset] = min(plans, key=lambda p: p["cost"])
    return best[frozenset(q.order)]


def written(q):
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
