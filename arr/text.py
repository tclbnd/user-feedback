"""Conversions between arrays and text (PostgreSQL)."""


def array_to_string(a, delim, null_string=None):
    if a is None or delim is None:
        return None
    return delim.join("" if x is None else str(x) for x in a)


def string_to_array(s, delim, null_string=None):
    if s is None:
        return None
    parts = s.split(delim)
    return [None if p == null_string else p for p in parts]


def array_out(a):
    """Text output, e.g. {1,2,NULL,"a b"}."""
    if a is None:
        return None
    items = []
    for x in a:
        if x is None:
            items.append("NULL")
        else:
            items.append(str(x))
    return "{" + ",".join(items) + "}"
