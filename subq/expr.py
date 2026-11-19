"""Row-by-row evaluation of WHERE clauses, including subqueries.

Predicates:
  {"cmp": {"left": term | [terms], "op": op, "right": term | [terms]}}
  {"in": {"left": term | [terms], "sub": subquery}}       also "not_in"
  {"exists": {"sub": subquery}}                           also "not_exists"
  {"any": {"left": term, "op": op, "values": [...]}}      x op ANY(array)
  {"all": {"left": term, "op": op, "values": [...]}}      x op ALL(array)
  {"distinct_from": [term, term]}
  {"and": [...]}, {"or": [...]}, {"not": p}, {"is_null": term}
Terms: {"col": name}, {"outer": name} (correlated reference), or a literal.
Subquery: {"from": table, "select": [cols], "where": pred | null}.
"""
from .logic import and_, compare, distinct_from, not_, or_, row_compare


def term(t, row, outer):
    if isinstance(t, dict) and "col" in t:
        return row.get(t["col"])
    if isinstance(t, dict) and "outer" in t:
        return outer.get(t["outer"])
    return t


def terms(t, row, outer):
    return [term(x, row, outer) for x in t] if isinstance(t, list) else [term(t, row, outer)]


def subquery_rows(db, sub, outer):
    rows = []
    for r in db[sub["from"]]:
        if sub.get("where") is None or evaluate(db, sub["where"], r, outer) is True:
            rows.append([r.get(c) for c in sub["select"]])
    return rows


def evaluate(db, p, row, outer=None):
    outer = outer if outer is not None else {}
    if "and" in p:
        return and_(evaluate(db, x, row, outer) for x in p["and"])
    if "or" in p:
        return or_(evaluate(db, x, row, outer) for x in p["or"])
    if "not" in p:
        return not_(evaluate(db, p["not"], row, outer))
    if "is_null" in p:
        return term(p["is_null"], row, outer) is None
    if "cmp" in p:
        c = p["cmp"]
        return row_compare(terms(c["left"], row, outer), c["op"], terms(c["right"], row, outer))
    if "distinct_from" in p:
        a, b = p["distinct_from"]
        return distinct_from(term(a, row, outer), term(b, row, outer))
    if "in" in p or "not_in" in p:
        spec = p.get("in") or p["not_in"]
        left = terms(spec["left"], row, outer)
        values = subquery_rows(db, spec["sub"], row)
        found = left in values
        return found if "in" in p else not found
    if "exists" in p or "not_exists" in p:
        spec = p.get("exists") or p["not_exists"]
        found = bool(subquery_rows(db, spec["sub"], row))
        return found if "exists" in p else not found
    if "any" in p:
        a = p["any"]
        x = term(a["left"], row, outer)
        return any(compare(x, a["op"], v) is True for v in a["values"])
    if "all" in p:
        a = p["all"]
        x = term(a["left"], row, outer)
        return all(compare(x, a["op"], v) is True for v in a["values"])
    raise ValueError(f"bad predicate {p}")


def filter_rows(db, table, where):
    return [r for r in db[table] if where is None or evaluate(db, where, r) is True]
