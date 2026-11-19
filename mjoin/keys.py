"""Join keys and join filters.

A join key is one column name or a list of column names (composite key,
compared column by column).  The sort node orders composite keys column by
column with NULLs last within each column, so ``(eu, NULL)`` sorts after
``(eu, 7)`` but before ``(us, 1)``.

A join filter is a list of extra conditions between the two sides,
``{"l": col, "op": op, "r": col}``, evaluated on rows whose keys are equal
(``ON l.k = r.k AND l.day >= r.valid_from``).  For outer joins, a left row
whose key matches but whose filter fails is still an unmatched row.
"""
import operator

OPS = {"=": operator.eq, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def columns(key):
    return list(key) if isinstance(key, (list, tuple)) else [key]


def key_of(row, key):
    cols = columns(key)
    return tuple(row.get(c) for c in cols) if len(cols) > 1 else row.get(cols[0])


def sort_key(value):
    if isinstance(value, tuple):
        return tuple((v is None, v if v is not None else 0) for v in value)
    return (value is None, value if value is not None else 0)


def has_null(value):
    return any(v is None for v in value) if isinstance(value, tuple) else value is None


def passes(filters, l, r):
    for f in filters or ():
        a, b = l.get(f["l"]), r.get(f["r"])
        if a is None or b is None or not OPS[f["op"]](a, b):
            return False
    return True


def describe(lkey, rkey, filters):
    parts = [f"l.{a} = r.{b}" for a, b in zip(columns(lkey), columns(rkey))]
    parts += [f"l.{f['l']} {f['op']} r.{f['r']}" for f in filters or ()]
    return " AND ".join(parts)
