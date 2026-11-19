"""Tiny expression language used in MERGE conditions and SET lists.

Values are literals, or strings ``"src.col"`` / ``"tgt.col"`` that refer to
the source row or the matched target row.  Comparisons follow SQL: anything
compared with NULL is unknown, and unknown counts as false in a WHEN clause.
"""


def value(term, src, tgt):
    if isinstance(term, str) and term.startswith("src."):
        return src.get(term[4:])
    if isinstance(term, str) and term.startswith("tgt."):
        return None if tgt is None else tgt.get(term[4:])
    if isinstance(term, dict) and "add" in term:
        a, b = (value(t, src, tgt) for t in term["add"])
        return None if a is None or b is None else a + b
    return term


def truth(cond, src, tgt):
    """Return True, False or None (unknown)."""
    if cond is None:
        return True
    if "and" in cond:
        parts = [truth(c, src, tgt) for c in cond["and"]]
        if False in parts:
            return False
        return None if None in parts else True
    if "or" in cond:
        parts = [truth(c, src, tgt) for c in cond["or"]]
        if True in parts:
            return True
        return None if None in parts else False
    if "not" in cond:
        t = truth(cond["not"], src, tgt)
        return None if t is None else not t
    if "is_null" in cond:
        return value(cond["is_null"], src, tgt) is None
    op, a, b = cond["cmp"]
    a, b = value(a, src, tgt), value(b, src, tgt)
    if a is None or b is None:
        return None
    return {"=": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
