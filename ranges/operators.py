"""Range functions and operators (PostgreSQL names)."""
from .rangetype import Range, RangeError


def _b(x):
    return "t" if x else "f"


def lower(r):
    return r.lower


def upper(r):
    return r.upper


def isempty(r):
    return _b(r.empty)


def lower_inc(r):
    return _b(r.lower_inc)


def upper_inc(r):
    return _b(r.upper_inc)


def lower_inf(r):
    return _b(r.lower is None)


def upper_inf(r):
    return _b(r.upper is None)


def _lo(r):
    return r.lower


def _hi(r):
    return r.upper


def contains_elem(r, x):
    return _b((r.lower is None or r.lower <= x) and (r.upper is None or x <= r.upper))


def contains(a, b):
    return _b((a.lower is None or (b.lower is not None and a.lower <= b.lower))
              and (a.upper is None or (b.upper is not None and b.upper <= a.upper)))


def contained(a, b):
    return contains(b, a)


def overlaps(a, b):
    return _b((a.upper is None or b.lower is None or b.lower <= a.upper)
              and (b.upper is None or a.lower is None or a.lower <= b.upper))


def adjacent(a, b):
    return _b(a.upper == b.lower or b.upper == a.lower)


def union(a, b):
    lo = None if a.lower is None or b.lower is None else min(a.lower, b.lower)
    hi = None if a.upper is None or b.upper is None else max(a.upper, b.upper)
    return Range(a.typ, lo, hi, a.lower_inc, a.upper_inc)


def intersection(a, b):
    lo = b.lower if a.lower is None else a.lower if b.lower is None else max(a.lower, b.lower)
    hi = b.upper if a.upper is None else a.upper if b.upper is None else min(a.upper, b.upper)
    return Range(a.typ, lo, hi, a.lower_inc, a.upper_inc)


def difference(a, b):
    if b.lower is not None and (a.lower is None or b.lower > a.lower):
        return Range(a.typ, a.lower, b.lower, a.lower_inc, False)
    return Range(a.typ, b.upper, a.upper, True, a.upper_inc)


def range_merge(a, b):
    return union(a, b)


def contains_any(a, b):
    return contains(a, b) if isinstance(b, Range) else contains_elem(a, b)


def less(a, b):
    return _b((a.lower, a.upper) < (b.lower, b.upper))


FUNCTIONS = {
    "lower": lower, "upper": upper, "isempty": isempty, "lower_inc": lower_inc, "upper_inc": upper_inc,
    "lower_inf": lower_inf, "upper_inf": upper_inf, "@>": contains_any, "<@": contained, "&&": overlaps,
    "-|-": adjacent, "+": union, "*": intersection, "-": difference, "range_merge": range_merge, "<": less,
}
