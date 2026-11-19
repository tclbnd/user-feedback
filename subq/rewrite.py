"""Subquery flattening.

Uncorrelated IN / NOT IN and correlated EXISTS / NOT EXISTS conjuncts at
the top of the WHERE clause are turned into hash semi-joins and
anti-joins: the subquery runs once into a hash table instead of once per
outer row.  The remaining conjuncts are evaluated row by row.  A rewritten
plan must return exactly what row-by-row evaluation returns.
"""
from .expr import evaluate, subquery_rows, terms


def _conjuncts(where):
    if where is None:
        return []
    return list(where["and"]) if "and" in where else [where]


def _flattenable(p):
    if "in" in p or "not_in" in p:
        spec = p.get("in") or p["not_in"]
        return "outer" not in str(spec["sub"])
    if "exists" in p or "not_exists" in p:
        spec = p.get("exists") or p["not_exists"]
        return bool(spec["sub"].get("corr"))
    return False


def plan(query):
    joins, rest = [], []
    for c in _conjuncts(query.get("where")):
        (joins if _flattenable(c) else rest).append(c)
    return joins, rest


def _hash_side(db, p):
    if "in" in p or "not_in" in p:
        spec = p.get("in") or p["not_in"]
        return {tuple(v) for v in subquery_rows(db, spec["sub"], {})}
    spec = p.get("exists") or p["not_exists"]
    sub = spec["sub"]
    keys = set()
    for r in db[sub["from"]]:
        if sub.get("where") is None or evaluate(db, sub["where"], r, {}) is True:
            keys.add(tuple(r.get(inner) for _, inner in sub["corr"]))
    return keys


def _probe(p, table, row):
    if "in" in p or "not_in" in p:
        spec = p.get("in") or p["not_in"]
        key = tuple(terms(spec["left"], row, {}))
        hit = key in table
        return hit if "in" in p else not hit
    spec = p.get("exists") or p["not_exists"]
    key = tuple(row.get(outer) for outer, _ in spec["sub"]["corr"])
    hit = key in table
    return hit if "exists" in p else not hit


def run(db, query):
    joins, rest = plan(query)
    tables = [(p, _hash_side(db, p)) for p in joins]
    out = []
    for row in db[query["from"]]:
        if not all(_probe(p, t, row) for p, t in tables):
            continue
        if all(evaluate(db, c, row) is True for c in rest):
            out.append(row)
    return out, {"semi_anti_joins": len(joins), "row_by_row": len(rest)}
