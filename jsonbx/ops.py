"""jsonb operators, evaluated row by row.

``@>`` (contains), ``?`` (key exists), ``?|`` (any key exists), ``?&`` (all
keys exist), with PostgreSQL's jsonb semantics.  These are the reference
the GIN indexes must agree with.
"""
from .value import kind


def _scalar_equal(a, b):
    return kind(a) == kind(b) and a == b


def contains(a, b):
    """a @> b"""
    ka, kb = kind(a), kind(b)
    if ka == "array" and kb != "array":
        # an array contains a non-array value if some element contains it
        return any(contains(x, b) for x in a)
    if ka != kb:
        return False
    if ka == "object":
        return all(k in a and contains(a[k], v) for k, v in b.items())
    if ka == "array":
        # unordered: every element of b must be contained in some element of a
        return all(any(contains(x, y) for x in a) for y in b)
    return _scalar_equal(a, b)


def exists(a, key):
    """a ? key — a top-level object key, or a top-level string array element"""
    if isinstance(a, dict):
        return key in a
    if isinstance(a, list):
        return any(isinstance(x, str) and x == key for x in a)
    return False


def exists_any(a, keys):
    return any(exists(a, k) for k in keys)


def exists_all(a, keys):
    return all(exists(a, k) for k in keys)


OPERATORS = {"@>": contains, "?": exists, "?|": exists_any, "?&": exists_all}
