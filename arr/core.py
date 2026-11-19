"""One-dimensional SQL arrays (PostgreSQL semantics), 1-based.

Values are Python lists; NULL elements are None; a NULL array is None.
"""


class ArrayError(Exception):
    pass


def cardinality(a):
    return None if a is None else len(a)


def array_length(a, dim=1):
    if a is None:
        return None
    return len(a)


def array_lower(a, dim=1):
    return None if a is None else 1


def array_upper(a, dim=1):
    return None if a is None else len(a)


def subscript(a, i):
    if a is None or i is None:
        return None
    return a[i - 1]


def slice_(a, lo, hi):
    if a is None:
        return None
    return a[lo - 1:hi]


def array_append(a, x):
    return (a or []) + [x]


def array_prepend(x, a):
    return [x] + (a or [])


def array_cat(a, b):
    return (a or []) + (b or [])


def array_remove(a, x):
    if a is None:
        return None
    return [e for e in a if e != x]


def array_replace(a, old, new):
    if a is None:
        return None
    return [new if e == old else e for e in a]


def array_position(a, x, start=1):
    if a is None:
        return None
    for i in range(start - 1, len(a)):
        if a[i] == x:
            return i + 1
    return None


def array_positions(a, x):
    if a is None:
        return None
    return [i + 1 for i, e in enumerate(a) if e == x]


def contains(a, b):
    """a @> b"""
    if a is None or b is None:
        return None
    return all(x in a for x in b)


def overlaps(a, b):
    """a && b"""
    if a is None or b is None:
        return None
    return any(x in a for x in b)


def trim_array(a, n):
    if a is None:
        return None
    return a[:len(a) - n]
