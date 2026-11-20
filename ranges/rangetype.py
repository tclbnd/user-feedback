"""Range values of the booking engine (PostgreSQL int4range / numrange / daterange).

A range is written like PostgreSQL range literals: '[1,5)', '(,10]',
'empty'.  Elements are ints, Decimals or dates depending on the type.

The semantics mirror PostgreSQL exactly: discrete ranges (int4range,
daterange) are stored in canonical '[)' form, a range whose bounds are
equal collapses to 'empty' unless both bounds are inclusive, a lower
bound greater than the upper bound is an error, int4range bounds are
32-bit integers, and unbounded bounds always print as '(' / ')'.
"""
import datetime
import re
from decimal import Decimal, InvalidOperation


class RangeError(Exception):
    pass


INT4_MIN = -2147483648
INT4_MAX = 2147483647


class _InfiniteDate:
    """The date values 'infinity' and '-infinity'.

    These are ordinary (extreme) values, not open bounds: upper_inf of
    daterange '(2026-11-20,infinity)' is false.
    """

    def __init__(self, sign, text):
        self.sign = sign
        self.text = text

    def __str__(self):
        return self.text

    __repr__ = __str__


DATE_INFINITY = _InfiniteDate(1, "infinity")
DATE_NEG_INFINITY = _InfiniteDate(-1, "-infinity")


_INT4_RE = re.compile(r"[+-]?\d+$")
_NUMERIC_RE = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _parse_int4(text):
    t = text.strip()
    if not _INT4_RE.match(t):
        raise RangeError('invalid input syntax for type integer: "%s"' % text)
    v = int(t)
    if v < INT4_MIN or v > INT4_MAX:
        raise RangeError('value "%s" is out of range for type integer' % text)
    return v


def _parse_numeric(text):
    t = text.strip()
    low = t.lower()
    if low.lstrip("+-") == "nan":
        return Decimal("NaN")
    if low.lstrip("+") in ("infinity", "inf"):
        return Decimal("Infinity")
    if low in ("-infinity", "-inf"):
        return Decimal("-Infinity")
    if not _NUMERIC_RE.match(t):
        raise RangeError('invalid input syntax for type numeric: "%s"' % text)
    try:
        return Decimal(t)
    except InvalidOperation:
        raise RangeError('invalid input syntax for type numeric: "%s"' % text)


def _parse_date(text):
    t = text.strip()
    low = t.lower()
    if low in ("infinity", "+infinity"):
        return DATE_INFINITY
    if low == "-infinity":
        return DATE_NEG_INFINITY
    try:
        return datetime.date.fromisoformat(t)
    except ValueError:
        raise RangeError('invalid input syntax for type date: "%s"' % text)


SUBTYPES = {
    "int4range": _parse_int4,
    "numrange": _parse_numeric,
    "daterange": _parse_date,
}

# int4range and daterange are discrete and use canonical '[)' form;
# numrange keeps the bounds exactly as given.
DISCRETE = {"int4range": True, "numrange": False, "daterange": True}


def cmp_elements(a, b):
    """Three-way compare of two range elements, PostgreSQL ordering.

    Numeric NaN sorts above every other value and equals itself; the
    date values 'infinity' / '-infinity' sort above / below every date.
    """
    ai = isinstance(a, _InfiniteDate)
    bi = isinstance(b, _InfiniteDate)
    if ai or bi:
        sa = a.sign if ai else 0
        sb = b.sign if bi else 0
        return (sa > sb) - (sa < sb)
    an = isinstance(a, Decimal) and a.is_nan()
    bn = isinstance(b, Decimal) and b.is_nan()
    if an or bn:
        if an and bn:
            return 0
        return 1 if an else -1
    return (a > b) - (a < b)


def element_text(v):
    """Render a range element the way PostgreSQL prints it."""
    if isinstance(v, _InfiniteDate):
        return v.text
    if isinstance(v, Decimal):
        if v.is_nan():
            return "NaN"
        if v.is_infinite():
            return "Infinity" if v > 0 else "-Infinity"
        s = str(v)
        if "E" in s:
            # PostgreSQL uses fixed notation in this exponent window,
            # scientific notation ('1.5E+20') outside it.
            e = v.adjusted()
            s = format(v, "f") if -6 <= e <= 15 else format(v, "E")
        return s
    return str(v)


def _succ(typ, v):
    """Successor of a bound value, for canonicalizing discrete ranges."""
    if typ == "int4range":
        if v >= INT4_MAX:
            raise RangeError("integer out of range")
        return v + 1
    if isinstance(v, _InfiniteDate):
        return v
    try:
        return v + datetime.timedelta(days=1)
    except OverflowError:
        raise RangeError("date out of range")


def make_range(typ, lower, upper, lower_inc, upper_inc):
    """Build a range exactly like PostgreSQL: validate the bounds,
    collapse degenerate ranges to 'empty' and put discrete ranges into
    canonical '[)' form."""
    if lower is None:
        lower_inc = False
    if upper is None:
        upper_inc = False
    if lower is not None and upper is not None:
        c = cmp_elements(lower, upper)
        if c > 0:
            raise RangeError(
                "range lower bound must be less than or equal to range upper bound")
        if c == 0 and not (lower_inc and upper_inc):
            return Range(typ, empty=True)
    if DISCRETE[typ]:
        if lower is not None and not lower_inc:
            lower = _succ(typ, lower)
            lower_inc = True
        if upper is not None and upper_inc:
            upper = _succ(typ, upper)
            upper_inc = False
        if (lower is not None and upper is not None
                and cmp_elements(lower, upper) >= 0):
            return Range(typ, empty=True)
    return Range(typ, lower, upper, lower_inc, upper_inc)


def _split_bounds(inner, literal):
    """Split the inside of a range literal at the (unquoted) comma."""
    parts, buf = [], []
    in_quotes = False
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and in_quotes:
            buf.append(ch)
            if i + 1 < len(inner):
                buf.append(inner[i + 1])
                i += 1
        elif ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == "," and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    if len(parts) != 2 or in_quotes:
        raise RangeError('malformed range literal: "%s"' % literal)
    return parts


def _unquote(s, literal):
    s = s.strip()
    if s.startswith('"'):
        if len(s) < 2 or not s.endswith('"'):
            raise RangeError('malformed range literal: "%s"' % literal)
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if '"' in s:
        raise RangeError('malformed range literal: "%s"' % literal)
    return s


class Range:
    def __init__(self, typ, lower=None, upper=None, lower_inc=False,
                 upper_inc=False, empty=False):
        self.typ, self.lower, self.upper = typ, lower, upper
        self.lower_inc, self.upper_inc, self.empty = lower_inc, upper_inc, empty

    @classmethod
    def parse(cls, typ, text):
        text = text.strip()
        if text.lower() == "empty":
            return cls(typ, empty=True)
        if len(text) < 2 or text[0] not in "[(" or text[-1] not in ")]":
            raise RangeError('malformed range literal: "%s"' % text)
        lo_s, hi_s = _split_bounds(text[1:-1], text)
        conv = SUBTYPES[typ]
        lower = conv(_unquote(lo_s, text)) if lo_s.strip() else None
        upper = conv(_unquote(hi_s, text)) if hi_s.strip() else None
        return make_range(typ, lower, upper, text[0] == "[", text[-1] == "]")

    def __str__(self):
        if self.empty:
            return "empty"
        if self.lower is None:
            s = "(,"
        else:
            s = ("[" if self.lower_inc else "(") + element_text(self.lower) + ","
        if self.upper is None:
            s += ")"
        else:
            s += element_text(self.upper) + ("]" if self.upper_inc else ")")
        return s

    __repr__ = __str__
