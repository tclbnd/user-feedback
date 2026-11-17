"""Rewrite subquery predicates into joins."""
from expr import SUBQUERY_OPS


def _non_null_columns(table, alias, where):
    cols = {f"{alias}.{c}" for c in table.get("not_null", [])}
    for pred in where:
        if pred["op"] == "is_not_null" and "col" in pred["arg"]:
            cols.add(pred["arg"]["col"])
    return cols


def _as_not_in(pred, outer_alias):
    """NOT EXISTS (... WHERE inner = outer AND ...) has the same join keys as NOT IN."""
    sub = pred["query"]
    args, select, rest = [], [], []
    for p in sub.get("where", []):
        sides = (p.get("left", {}).get("col", ""), p.get("right", {}).get("col", ""))
        if p["op"] == "=" and sides[1].startswith(outer_alias + ".") and sides[0].startswith(sub["as"] + "."):
            args.append(p["right"])
            select.append(sides[0])
        elif p["op"] == "=" and sides[0].startswith(outer_alias + ".") and sides[1].startswith(sub["as"] + "."):
            args.append(p["left"])
            select.append(sides[1])
        else:
            rest.append(p)
    return {"op": "not_in", "args": args, "query": dict(sub, select=select, where=rest)}


def plan(query, db, rewrite=True):
    nodes = []
    for pred in query.get("where", []):
        op = pred["op"]
        if op not in SUBQUERY_OPS:
            nodes.append(("filter", pred))
        elif not rewrite:
            nodes.append(("subplan", pred))
        elif op in ("in", "exists"):
            nodes.append(("semi_join", pred))
        else:
            if op == "not_exists":
                pred = _as_not_in(pred, query["as"])
            sub = pred["query"]
            inner_known = _non_null_columns(db[sub["from"]], sub["as"], sub.get("where", []))
            safe = all(c in inner_known for c in sub["select"])
            nodes.append(("anti_join" if safe else "null_aware_anti_join", pred))
    return nodes
