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
    if isinstance(t, list):
        return [term(x, row, outer) for x in t]
    return t


def terms(t, row, outer):
    return [term(x, row, outer) for x in t] if isinstance(t, list) else [term(t, row, outer)]


def value(t, row, outer):
    """Resolve a term; a row-valued term stays a list rather than a 1-list."""
    return term(t, row, outer)


def subquery_rows(db, sub, current_row):
    """Rows produced by a subquery: a list of value lists.

    ``current_row`` is the row of the enclosing query level; the subquery
    sees it as its ``outer`` row, so {"outer": col} terms in the WHERE
    resolve against it.  A ``corr`` list of ``[outer_col, inner_col]`` pairs
    adds ``inner = outer`` equalities; because ``=`` is three-valued a NULL
    on either side cannot match, exactly as in SQL.
    """
    corr = sub.get("corr") or []
    rows = []
    for r in db[sub["from"]]:
        if sub.get("where") is not None and evaluate(db, sub["where"], r, current_row) is not True:
            continue
        if any(compare(r.get(inner), "=", current_row.get(outer)) is not True
               for outer, inner in corr):
            continue
        rows.append([r.get(c) for c in sub["select"]])
    return rows


def _in_result(db, spec, row, outer):
    """SQL value of ``left IN (subquery)`` before any NOT is applied.

    IN is ``left = ANY(subquery)``: a three-valued OR over the element
    equalities.  A NULL on the left, or a NULL in a compared subquery
    column, yields unknown unless some row compares equal (True wins over
    unknown).  An empty subquery yields False.
    """
    left = terms(spec["left"], row, outer)
    return or_(row_compare(left, "=", v) for v in subquery_rows(db, spec["sub"], row))


def _exists_result(db, spec, row, outer):
    """SQL value of ``EXISTS (subquery)``: True iff it produces any row."""
    return bool(subquery_rows(db, spec["sub"], row))


def evaluate(db, p, row, outer=None):
    outer = outer if outer is not None else {}
    if "and" in p:
        return and_(evaluate(db, x, row, outer) for x in p["and"])
    if "or" in p:
        return or_(evaluate(db, x, row, outer) for x in p["or"])
    if "not" in p:
        return not_(evaluate(db, p["not"], row, outer))
    if "is_null" in p:
        v = value(p["is_null"], row, outer)
        if isinstance(v, list):
            return all(x is None for x in v)
        return v is None
    if "cmp" in p:
        c = p["cmp"]
        return row_compare(terms(c["left"], row, outer), c["op"], terms(c["right"], row, outer))
    if "distinct_from" in p:
        a, b = p["distinct_from"]
        return distinct_from(value(a, row, outer), value(b, row, outer))
    if "in" in p or "not_in" in p:
        spec = p.get("in") or p["not_in"]
        result = _in_result(db, spec, row, outer)
        return result if "in" in p else not_(result)
    if "exists" in p or "not_exists" in p:
        spec = p.get("exists") or p["not_exists"]
        result = _exists_result(db, spec, row, outer)
        return result if "exists" in p else not_(result)
    if "any" in p:
        a = p["any"]
        x = term(a["left"], row, outer)
        return or_(compare(x, a["op"], v) for v in a["values"])
    if "all" in p:
        a = p["all"]
        x = term(a["left"], row, outer)
        return and_(compare(x, a["op"], v) for v in a["values"])
    raise ValueError(f"bad predicate {p}")


def filter_rows(db, table, where):
    return [r for r in db[table] if where is None or evaluate(db, where, r) is True]
