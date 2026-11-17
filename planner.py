"""Join planning: cost-based join order by dynamic programming over subsets."""
from itertools import combinations

from cost import table_of


def scan(q, table):
    # Filters push into the scan, except on a left-joined table: its scan
    # stays unfiltered and the filters are applied on its left join node.
    filters = [] if table in q.nullable else [f for f in q.filters if table_of(f["col"]) == table]
    rows = q.scan_rows(table, filters)
    return {"op": "scan", "table": table, "filters": filters, "rows": rows,
            "cost": q.stats[table]["rows"], "tables": frozenset([table])}


def left_join(q, left, table):
    """A left-joined table enters alone, as the right input of its own left
    join, once every other table named in its `on` is on the left."""
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
    for side in (left, right):
        if len(side["tables"]) == 1 and side["tables"] <= set(q.nullable):
            return None  # a left-joined table never inner joins by itself
    span = q.spanning(left["tables"], right["tables"])
    on = [[lc[0], rc[0]] for lc, rc in span]
    rows = q.inner_rows(left["rows"], right["rows"], left["tables"], right["tables"])
    return {"op": "join", "type": "inner" if on else "cross", "left": left, "right": right, "on": on,
            "filters": [], "rows": rows, "cost": left["cost"] + right["cost"] + rows,
            "tables": left["tables"] | right["tables"]}


def _connected(q):
    """Is the query's join graph (equivalence classes plus left-join edges)
    connected? Only a disconnected graph may need cross products."""
    adj = {t: set() for t in q.order}
    for cls in q.classes:
        ts = [table_of(c) for c in cls]
        for t in ts:
            adj[t].update(ts)
    for t, on in q.nullable.items():
        for pair in on:
            for col in pair:
                adj[t].add(table_of(col))
                adj[table_of(col)].add(t)
    seen, stack = set(), [q.order[0]]
    while stack:
        t = stack.pop()
        if t not in seen:
            seen.add(t)
            stack.extend(adj[t])
    return seen == set(q.order)


def _better(plan, cand):
    return cand is not None and (plan is None or cand["cost"] < plan["cost"])


def choose(q):
    """Pick the cheapest plan by DP over table subsets: best[S] is the
    cheapest plan covering exactly the tables S."""
    tables = list(q.order)
    nullable = set(q.nullable)
    connected = _connected(q)
    best = {frozenset([t]): scan(q, t) for t in tables}
    for size in range(2, len(tables) + 1):
        for combo in combinations(tables, size):
            subset = frozenset(combo)
            plan = None
            # A left-joined table enters alone, on the right of its left join.
            for t in combo:
                if t in nullable and subset - {t} in best:
                    cand = left_join(q, best[subset - {t}], t)
                    if _better(plan, cand):
                        plan = cand
            # Inner-join splits of the subset into two parts.
            if q.shape == "left_deep":
                splits = [(subset - {t}, frozenset([t])) for t in combo]
            else:
                first, rest = combo[0], combo[1:]
                splits = [(subset - frozenset(rc), frozenset(rc))
                          for r in range(1, len(rest) + 1)
                          for rc in combinations(rest, r)]
                # `first` stays on the left, so each split is considered once.
            for lset, rset in splits:
                left, right = best.get(lset), best.get(rset)
                if left is None or right is None:
                    continue
                if connected and not q.spanning(lset, rset):
                    continue  # no cross products while a join path exists
                cand = inner_join(q, left, right)
                if _better(plan, cand):
                    plan = cand
            if plan is not None:
                best[subset] = plan
    return best[frozenset(tables)]


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
