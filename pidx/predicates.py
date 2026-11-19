"""Predicate trees and evaluation (SQL three-valued logic).

    {"and": [p, ...]}  {"or": [p, ...]}
    {"cmp": [col, op, const]}      op in = != < <= > >=
    {"in": [col, [c1, c2, ...]]}
    {"is_null": col}   {"not_null": col}
"""
import json
import operator

OPS = {"=": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def truth(p, row):
    if "and" in p:
        v = [truth(x, row) for x in p["and"]]
        return False if False in v else (None if None in v else True)
    if "or" in p:
        v = [truth(x, row) for x in p["or"]]
        return True if True in v else (None if None in v else False)
    if "is_null" in p:
        return row.get(p["is_null"]) is None
    if "not_null" in p:
        return row.get(p["not_null"]) is not None
    if "in" in p:
        col, vals = p["in"]
        x = row.get(col)
        return None if x is None else x in vals
    col, op, c = p["cmp"]
    x = row.get(col)
    if x is None:
        return None
    try:
        return OPS[op](x, c)
    except TypeError:
        return None


def canonical(p):
    """Stable text form used to compare predicates."""
    return json.dumps(p, sort_keys=True)
