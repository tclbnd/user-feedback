"""SQL three-valued logic: True, False and None (unknown)."""
import operator

OPS = {"=": operator.eq, "<>": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def compare(a, op, b):
    """Scalar comparison: unknown if either side is NULL."""
    if a is None or b is None:
        return None
    return OPS[op](a, b)


def and_(values):
    values = list(values)
    if False in values:
        return False
    return None if None in values else True


def or_(values):
    values = list(values)
    if True in values:
        return True
    return None if None in values else False


def not_(v):
    return None if v is None else not v


def row_compare(left, op, right):
    """Row-wise comparison (a, b, ...) op (c, d, ...)."""
    if len(left) != len(right):
        raise ValueError("row size mismatch")
    if len(left) == 1:
        return compare(left[0], op, right[0])
    if any(x is None for x in left) or any(y is None for y in right):
        return None
    return OPS[op](tuple(left), tuple(right))


def distinct_from(a, b):
    """a IS DISTINCT FROM b (never unknown)."""
    if a is None or b is None:
        return not (a is None and b is None)
    return a != b
