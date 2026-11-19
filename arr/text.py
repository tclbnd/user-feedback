"""Conversions between arrays and text (PostgreSQL)."""

import math

from .core import _as_array


# Characters that force an element to be quoted in array text output.
# PostgreSQL's array output quotes element values that are empty, contain
# curly braces, the delimiter (comma), double quotes, backslashes or white
# space, or that match the word NULL (case-insensitively).
_SPECIAL = set('{},"\\')
_SPACE = set(" \t\n\r\v\f")


def _out_text(x):
    """Element output function: how PostgreSQL renders a value as text."""
    if isinstance(x, bool):
        # boolout
        return "t" if x else "f"
    if isinstance(x, float):
        # float8out: shortest round-trip form, without a needless ".0".
        if math.isnan(x):
            return "NaN"
        if math.isinf(x):
            return "Infinity" if x > 0 else "-Infinity"
        s = repr(x)
        if s.endswith(".0"):
            s = s[:-2]
        return s
    return str(x)


def _needs_quote(s):
    if s == "":
        return True
    # Unquoted NULL (in any case) means a NULL element, so quote it.
    if s.lower() == "null":
        return True
    return any(c in _SPECIAL or c in _SPACE for c in s)


def _quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def array_to_string(a, delim, null_string=None):
    """Join element texts with delim; NULL elements are skipped unless a
    null_string is supplied (a NULL null_string also skips them)."""
    a = _as_array(a)
    if a is None or delim is None:
        return None
    delim = _out_text(delim)
    parts = []
    for x in a:
        if x is None:
            if null_string is None:
                continue
            parts.append(_out_text(null_string))
        else:
            parts.append(_out_text(x))
    return delim.join(parts)


def string_to_array(s, delim, null_string=None):
    """Split text at delim into an array.

    An empty input yields an empty array.  A NULL delimiter splits into
    single characters; an empty delimiter yields the whole string as one
    field.  Fields equal to null_string (when it is not NULL) become NULL.
    """
    if s is None:
        return None
    if not isinstance(s, str):
        s = _out_text(s)
    if delim is not None and not isinstance(delim, str):
        delim = _out_text(delim)
    if null_string is not None:
        null_string = _out_text(null_string)

    # PostgreSQL returns a zero-element array for an empty input string,
    # whatever the delimiter or null_string.
    if s == "":
        return []

    if delim is None:
        parts = list(s)
    elif delim == "":
        parts = [s]
    else:
        parts = s.split(delim)

    if null_string is not None:
        parts = [None if p == null_string else p for p in parts]
    return parts


def array_out(a):
    """Array text output, e.g. {1,2,NULL,"a b"}."""
    a = _as_array(a)
    if a is None:
        return None
    items = []
    for x in a:
        if x is None:
            items.append("NULL")
        else:
            text = _out_text(x)
            items.append(_quote(text) if _needs_quote(text) else text)
    return "{" + ",".join(items) + "}"
