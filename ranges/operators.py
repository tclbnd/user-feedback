"""Range functions and operators (PostgreSQL names)."""
from .rangetype import Range, RangeError, cmp_elements, make_range


def _b(x):
    return "t" if x else "f"


def _check_types(a, b):
    if a.typ != b.typ:
        raise RangeError("range types do not match")


# --- bound comparisons (PostgreSQL range_cmp_bounds) ---
#
# A lower bound sorts before another if it reaches further down: an
# infinite (absent) bound first, then by value, and for equal values an
# inclusive bound before an exclusive one.  An upper bound sorts the
# other way: infinite last, and for equal values inclusive after
# exclusive.

def _cmp_lower(a, b):
    if a.lower is None:
        return 0 if b.lower is None else -1
    if b.lower is None:
        return 1
    c = cmp_elements(a.lower, b.lower)
    if c:
        return c
    if a.lower_inc != b.lower_inc:
        return -1 if a.lower_inc else 1
    return 0


def _cmp_upper(a, b):
    if a.upper is None:
        return 0 if b.upper is None else 1
    if b.upper is None:
        return -1
    c = cmp_elements(a.upper, b.upper)
    if c:
        return c
    if a.upper_inc != b.upper_inc:
        return 1 if a.upper_inc else -1
    return 0


# --- accessor functions ---

def lower(r):
    return None if r.empty else r.lower


def upper(r):
    return None if r.empty else r.upper


def isempty(r):
    return _b(r.empty)


def lower_inc(r):
    return _b(not r.empty and r.lower_inc)


def upper_inc(r):
    return _b(not r.empty and r.upper_inc)


def lower_inf(r):
    return _b(not r.empty and r.lower is None)


def upper_inf(r):
    return _b(not r.empty and r.upper is None)


# --- predicates (boolean workhorses) ---

def _contains(a, b):
    if b.empty:
        return True
    if a.empty:
        return False
    return _cmp_lower(a, b) <= 0 and _cmp_upper(a, b) >= 0


def _contains_elem(r, x):
    if r.empty:
        return False
    if r.lower is not None:
        c = cmp_elements(x, r.lower)
        if c < 0 or (c == 0 and not r.lower_inc):
            return False
    if r.upper is not None:
        c = cmp_elements(x, r.upper)
        if c > 0 or (c == 0 and not r.upper_inc):
            return False
    return True


def _strictly_left(a, b):
    """a is entirely left of b (PostgreSQL's <<)."""
    if a.empty or b.empty or a.upper is None or b.lower is None:
        return False
    c = cmp_elements(a.upper, b.lower)
    return c < 0 or (c == 0 and not (a.upper_inc and b.lower_inc))


def _overlaps(a, b):
    return (not a.empty and not b.empty
            and not _strictly_left(a, b) and not _strictly_left(b, a))


def _adjacent(a, b):
    if a.empty or b.empty:
        return False
    if (a.upper is not None and b.lower is not None
            and cmp_elements(a.upper, b.lower) == 0):
        return a.upper_inc != b.lower_inc
    if (b.upper is not None and a.lower is not None
            and cmp_elements(b.upper, a.lower) == 0):
        return b.upper_inc != a.lower_inc
    return False


def contains_any(a, b):
    """@> : range contains range, or range contains element."""
    if isinstance(b, Range):
        _check_types(a, b)
        return _b(_contains(a, b))
    return _b(_contains_elem(a, b))


def contained_any(a, b):
    """<@ : range is contained by range, or element is contained by range."""
    if isinstance(a, Range):
        _check_types(a, b)
        return _b(_contains(b, a))
    return _b(_contains_elem(b, a))


def overlaps(a, b):
    _check_types(a, b)
    return _b(_overlaps(a, b))


def adjacent(a, b):
    _check_types(a, b)
    return _b(_adjacent(a, b))


# --- set operations ---

def union(a, b):
    _check_types(a, b)
    if a.empty:
        return b
    if b.empty:
        return a
    if not _overlaps(a, b) and not _adjacent(a, b):
        raise RangeError("result of range union would not be contiguous")
    lo, lo_inc = (a.lower, a.lower_inc) if _cmp_lower(a, b) <= 0 else (b.lower, b.lower_inc)
    hi, hi_inc = (a.upper, a.upper_inc) if _cmp_upper(a, b) >= 0 else (b.upper, b.upper_inc)
    return make_range(a.typ, lo, hi, lo_inc, hi_inc)


def intersection(a, b):
    _check_types(a, b)
    if a.empty or b.empty:
        return Range(a.typ, empty=True)
    lo, lo_inc = (a.lower, a.lower_inc) if _cmp_lower(a, b) >= 0 else (b.lower, b.lower_inc)
    hi, hi_inc = (a.upper, a.upper_inc) if _cmp_upper(a, b) <= 0 else (b.upper, b.upper_inc)
    if lo is not None and hi is not None:
        c = cmp_elements(lo, hi)
        if c > 0 or (c == 0 and not (lo_inc and hi_inc)):
            return Range(a.typ, empty=True)
    return make_range(a.typ, lo, hi, lo_inc, hi_inc)


def difference(a, b):
    _check_types(a, b)
    if a.empty or b.empty or not _overlaps(a, b):
        return a
    cl = _cmp_lower(a, b)
    cu = _cmp_upper(a, b)
    if cl < 0 and cu > 0:
        raise RangeError("result of range difference would not be contiguous")
    if cl < 0:
        return make_range(a.typ, a.lower, b.lower, a.lower_inc, not b.lower_inc)
    if cu > 0:
        return make_range(a.typ, b.upper, a.upper, not b.upper_inc, a.upper_inc)
    return Range(a.typ, empty=True)


def range_merge(a, b):
    """Smallest range that includes both inputs; never fails."""
    _check_types(a, b)
    if a.empty:
        return b
    if b.empty:
        return a
    lo, lo_inc = (a.lower, a.lower_inc) if _cmp_lower(a, b) <= 0 else (b.lower, b.lower_inc)
    hi, hi_inc = (a.upper, a.upper_inc) if _cmp_upper(a, b) >= 0 else (b.upper, b.upper_inc)
    return make_range(a.typ, lo, hi, lo_inc, hi_inc)


# --- ordering ---

def _compare(a, b):
    """PostgreSQL range ordering: empty ranges sort first, then by
    lower bound, then by upper bound."""
    if a.empty and b.empty:
        return 0
    if a.empty:
        return -1
    if b.empty:
        return 1
    c = _cmp_lower(a, b)
    if c:
        return c
    return _cmp_upper(a, b)


def less(a, b):
    _check_types(a, b)
    return _b(_compare(a, b) < 0)


FUNCTIONS = {
    "lower": lower, "upper": upper, "isempty": isempty, "lower_inc": lower_inc, "upper_inc": upper_inc,
    "lower_inf": lower_inf, "upper_inf": upper_inf, "@>": contains_any, "<@": contained_any, "&&": overlaps,
    "-|-": adjacent, "+": union, "*": intersection, "-": difference, "range_merge": range_merge, "<": less,
}
