"""SQL three-valued logic: True, False and None (unknown)."""
import operator

OPS = {"=": operator.eq, "<>": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}

_ORDERED = ("<", "<=", ">", ">=")


def compare(a, op, b):
    """Scalar comparison: unknown if either side is NULL."""
    if a is None or b is None:
        return None
    return OPS[op](a, b)


def and_(values):
    """Three-valued AND: False beats unknown, unknown beats True."""
    result = True
    for v in values:
        if v is False:
            return False
        if v is None:
            result = None
    return result


def or_(values):
    """Three-valued OR: True beats unknown, unknown beats False."""
    result = False
    for v in values:
        if v is True:
            return True
        if v is None:
            result = None
    return result


def not_(v):
    return None if v is None else not v


def row_compare(left, op, right):
    """Row-wise comparison (a, b, ...) op (c, d, ...).

    Follows SQL/PostgreSQL semantics: comparisons are element-wise under
    three-valued logic.  ``=`` is the AND of the element equalities and
    ``<>`` the OR of the element inequalities.  The ordering operators are
    lexicographic: ``<`` is the OR over columns of "all earlier columns are
    equal and this one is smaller", and ``<=``/``>``/``>=`` are built from
    ``<`` and row equality:

        (a1, a2) <  (b1, b2)  ==  a1 < b1  OR  (a1 = b1 AND a2 < b2)
        (a1, a2) <= (b1, b2)  ==  (a1, a2) < (b1, b2)  OR  (a1, a2) = (b1, b2)

    A NULL in a later column therefore does not make the whole comparison
    unknown once an earlier column has decided it, e.g.
    ``(1, 5) < (2, NULL)`` is true, not unknown.
    """
    if len(left) != len(right):
        raise ValueError("row size mismatch")
    if len(left) == 1:
        return compare(left[0], op, right[0])
    if op == "=":
        return and_(compare(a, "=", b) for a, b in zip(left, right))
    if op == "<>":
        return or_(compare(a, "<>", b) for a, b in zip(left, right))
    if op not in _ORDERED:
        raise ValueError(f"bad comparison operator {op}")
    if op == "<=":
        return or_([row_compare(left, "<", right), row_compare(left, "=", right)])
    if op == ">":
        return row_compare(right, "<", left)
    if op == ">=":
        return or_([row_compare(right, "<", left), row_compare(left, "=", right)])
    # op is "<": walk the columns left to right; each term requires all
    # preceding columns to be equal and the current column to be smaller.
    equal_so_far = []
    terms = []
    for a, b in zip(left, right):
        terms.append(and_(equal_so_far + [compare(a, "<", b)]))
        equal_so_far.append(compare(a, "=", b))
    return or_(terms)


def distinct_from(a, b):
    """a IS DISTINCT FROM b (never unknown).  Also accepts row values."""
    if isinstance(a, list) or isinstance(b, list):
        if not (isinstance(a, list) and isinstance(b, list)):
            raise ValueError("cannot compare a row with a scalar")
        if len(a) != len(b):
            raise ValueError("row size mismatch")
        return or_(distinct_from(x, y) for x, y in zip(a, b))
    if a is None or b is None:
        return not (a is None and b is None)
    return a != b
