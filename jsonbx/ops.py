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
    if ka == "array" and kb not in ("array", "object"):
        # an array contains a primitive value it has as an element
        return any(contains(x, b) for x in a)
    if ka != kb:
        return False
    if ka == "object":
        return all(k in a and contains(a[k], v) for k, v in b.items())
    if ka == "array":
        return len(b) <= len(a) and all(contains(x, y) for x, y in zip(a, b))
    return _scalar_equal(a, b)


def exists(a, key):
    """a ? key"""
    if isinstance(a, dict):
        return key in a or any(exists(v, key) for v in a.values())
    if isinstance(a, list):
        return any(exists(v, key) for v in a)
    return False


def exists_any(a, keys):
    return any(exists(a, k) for k in keys)


def exists_all(a, keys):
    return all(exists(a, k) for k in keys)


OPERATORS = {"@>": contains, "?": exists, "?|": exists_any, "?&": exists_all}
