"""One-dimensional SQL arrays (PostgreSQL semantics), 1-based.

Values are Python lists; NULL elements are None; a NULL array is None.

Every function mirrors what PostgreSQL returns (or raises) for the same
input, including NULL arrays, NULL elements, empty arrays, out-of-range
subscripts and slices, and the containment/overlap operators.
"""


class ArrayError(Exception):
    pass


def _as_array(a, name="array"):
    """Reject inputs that are not an array (or NULL) with an engine error."""
    if a is None or isinstance(a, list):
        return a
    raise ArrayError("%s must be an array, got %s" % (name, type(a).__name__))


def _to_int(v, message):
    """Coerce an integral JSON number to int, else raise like PostgreSQL."""
    if isinstance(v, bool):
        raise ArrayError(message)
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    raise ArrayError(message)


def _same(x, y):
    """Element equality as IS NOT DISTINCT FROM.

    PostgreSQL compares elements of @> and && this way too: two NULL
    elements count as equal, while NULL never matches a non-NULL value.
    The same rule underlies array_position, array_remove, array_replace.
    """
    if x is None or y is None:
        return x is None and y is None
    return x == y


def cardinality(a):
    a = _as_array(a)
    return None if a is None else len(a)


def array_length(a, dim=1):
    # A NULL array, a NULL or non-existent dimension and an empty array
    # (which has no dimensions) all yield NULL.
    a = _as_array(a)
    if a is None or dim is None:
        return None
    dim = _to_int(dim, "array dimension must be an integer")
    if dim != 1 or len(a) == 0:
        return None
    return len(a)


def array_lower(a, dim=1):
    a = _as_array(a)
    if a is None or dim is None:
        return None
    dim = _to_int(dim, "array dimension must be an integer")
    if dim != 1 or len(a) == 0:
        return None
    return 1


def array_upper(a, dim=1):
    a = _as_array(a)
    if a is None or dim is None:
        return None
    dim = _to_int(dim, "array dimension must be an integer")
    if dim != 1 or len(a) == 0:
        return None
    return len(a)


def subscript(a, i):
    # A NULL array or NULL subscript yields NULL; a subscript outside the
    # array bounds also yields NULL (PostgreSQL does not raise).
    a = _as_array(a)
    if a is None or i is None:
        return None
    i = _to_int(i, "array subscript must have type integer")
    if i < 1 or i > len(a):
        return None
    return a[i - 1]


def slice_(a, lo, hi):
    # A NULL array or either NULL bound yields NULL.  Bounds are clamped to
    # the array; an empty or reversed range yields an empty array.
    a = _as_array(a)
    if a is None or lo is None or hi is None:
        return None
    lo = _to_int(lo, "array subscript must have type integer")
    hi = _to_int(hi, "array subscript must have type integer")
    lower = max(lo, 1)
    upper = min(hi, len(a))
    if upper < lower:
        return []
    return a[lower - 1:upper]


def array_append(a, x):
    # Strict on the array: array_append(NULL, x) is NULL, while a NULL
    # element is appended normally.
    a = _as_array(a)
    if a is None:
        return None
    return a + [x]


def array_prepend(x, a):
    a = _as_array(a)
    if a is None:
        return None
    return [x] + a


def array_cat(a, b):
    # array_cat treats a NULL array as empty; only NULL || NULL is NULL.
    a = _as_array(a)
    b = _as_array(b)
    if a is None and b is None:
        return None
    if a is None:
        return list(b)
    if b is None:
        return list(a)
    return a + b


def array_remove(a, x):
    a = _as_array(a)
    if a is None:
        return None
    return [e for e in a if not _same(e, x)]


def array_replace(a, old, new):
    a = _as_array(a)
    if a is None:
        return None
    return [new if _same(e, old) else e for e in a]


def array_position(a, x, start=1):
    # A NULL array yields NULL.  The searched value may be NULL, matching
    # NULL elements (IS NOT DISTINCT FROM semantics), so the function cannot
    # be strict.  The start argument is likewise optional: a NULL start means
    # "not specified" and the search begins at the first element.  A start
    # below 1 is an error.
    a = _as_array(a)
    if a is None:
        return None
    if start is not None:
        start = _to_int(start, "initial position must not be negative")
        if start < 1:
            raise ArrayError("initial position must not be negative")
    else:
        start = 1
    for i in range(start - 1, len(a)):
        if _same(a[i], x):
            return i + 1
    return None


def array_positions(a, x):
    a = _as_array(a)
    if a is None:
        return None
    return [i + 1 for i, e in enumerate(a) if _same(e, x)]


def contains(a, b):
    """a @> b: every element of b equals some element of a.

    Empty b is contained in anything (including an empty a); NULL elements
    match each other but nothing else.
    """
    a = _as_array(a)
    b = _as_array(b)
    if a is None or b is None:
        return None
    return all(any(_same(y, x) for x in a) for y in b)


def overlaps(a, b):
    """a && b: the arrays share at least one element (NULL matches NULL)."""
    a = _as_array(a)
    b = _as_array(b)
    if a is None or b is None:
        return None
    return any(_same(x, y) for x in a for y in b)


def trim_array(a, n):
    a = _as_array(a)
    if a is None or n is None:
        return None
    n = _to_int(n, "number of elements to trim must not be negative")
    if n < 0:
        raise ArrayError("number of elements to trim must not be negative")
    if n > len(a):
        raise ArrayError(
            "number of elements to trim must be between 0 and %d" % len(a))
    return a[:len(a) - n]
