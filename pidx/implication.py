"""Does a query's WHERE clause imply a partial index's predicate?

The planner may only use a partial index when every row the query can
return satisfies the index predicate; otherwise rows outside the index
would be missed.
"""
from .predicates import canonical


def _conjuncts(p):
    return p["and"] if "and" in p else [p]


def implies(query, index_pred):
    q = {canonical(c) for c in _conjuncts(query)}
    for need in _conjuncts(index_pred):
        if canonical(need) in q:
            continue
        if "cmp" in need and _range_hint(query, need):
            continue
        return False
    return True


def _range_hint(query, need):
    col, op, c = need["cmp"]
    for have in _conjuncts(query):
        if "cmp" in have and have["cmp"][0] == col and have["cmp"][1] == op and op in (">", ">=", "<", "<="):
            # same direction and column: the narrower range is covered by the index range
            return True
    return False
