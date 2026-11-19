"""Type input and casts of the import/ETL layer (PostgreSQL rules).

  cast(value, from_type, to_type)   from_type is "text" for input from text
Types: int2, int4, int8, numeric, numeric(p,s), float8, bool, text,
varchar(n), char(n).

Everything mirrors PostgreSQL: text input syntax, which casts exist at
all, rounding and range/overflow rules, and psql's text output.  Any
conversion PostgreSQL would reject raises CastError here.
"""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import math
import re


class CastError(Exception):
    pass


INT_RANGES = {
    "int2": (-(2 ** 15), 2 ** 15 - 1),
    "int4": (-(2 ** 31), 2 ** 31 - 1),
    "int8": (-(2 ** 63), 2 ** 63 - 1),
}

SQL_NAMES = {
    "int2": "smallint",
    "int4": "integer",
    "int8": "bigint",
    "numeric": "numeric",
    "float8": "double precision",
    "bool": "boolean",
    "text": "text",
    "varchar": "character varying",
    "char": "bpchar",
}

_ALIASES = {
    "int": "int4",
    "integer": "int4",
    "smallint": "int2",
    "bigint": "int8",
    "float": "float8",
    "double precision": "float8",
    "boolean": "bool",
    "decimal": "numeric",
    "character varying": "varchar",
    "character": "char",
    "bpchar": "char",
}

# Limits for unconstrained numeric values (NUMERIC_WEIGHT_MAX/_DSCALE_MAX).
_NUMERIC_MAX_INT_DIGITS = 65536
_NUMERIC_MAX_FRAC_DIGITS = 16383

_INT_TEXT_RE = re.compile(r"[+-]?\d+")
_NUMERIC_TEXT_RE = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")
_TYPMOD_RE = re.compile(r"(numeric|varchar|char)\(\s*(\d+)\s*(?:,\s*(-?\d+)\s*)?\)")

_WS = " \t\n\r\f\v"

# bool input accepts any unambiguous prefix of these words (case-insensitive).
_BOOL_WORDS = (
    ("true", True), ("false", False),
    ("yes", True), ("no", False),
    ("on", True), ("off", False),
    ("1", True), ("0", False),
)


def normalize_type(typ):
    """Map SQL aliases to the canonical names used here (keep any (p,s)/(n))."""
    base, sep, rest = typ.strip().lower().partition("(")
    base = " ".join(base.split())
    base = _ALIASES.get(base, base)
    return base + sep + rest


def _cannot(src, dst):
    raise CastError("cannot cast type %s to %s"
                    % (SQL_NAMES.get(src, src), SQL_NAMES.get(dst, dst)))


# ------------------------------------------------------------- input from text

def _int_from_text(s, dst):
    t = s.strip(_WS)
    if not _INT_TEXT_RE.fullmatch(t):
        raise CastError('invalid input syntax for type %s: "%s"' % (SQL_NAMES[dst], s))
    v = int(t)
    lo, hi = INT_RANGES[dst]
    if not lo <= v <= hi:
        raise CastError('value "%s" is out of range for type %s' % (t, SQL_NAMES[dst]))
    return v


def _check_numeric_limits(d):
    if not d.is_finite():
        return
    frac = max(-d.as_tuple().exponent, 0)
    if frac > _NUMERIC_MAX_FRAC_DIGITS:
        raise CastError("value overflows numeric format")
    if d != 0 and max(d.adjusted() + 1, 0) > _NUMERIC_MAX_INT_DIGITS:
        raise CastError("value overflows numeric format")


def _numeric_from_text(s):
    t = s.strip(_WS)
    low = t.lower()
    if low in ("nan", "+nan", "-nan"):
        return Decimal("NaN")
    if low in ("infinity", "inf", "+infinity", "+inf"):
        return Decimal("Infinity")
    if low in ("-infinity", "-inf"):
        return Decimal("-Infinity")
    if not _NUMERIC_TEXT_RE.fullmatch(t):
        raise CastError('invalid input syntax for type numeric: "%s"' % s)
    d = Decimal(t)
    _check_numeric_limits(d)
    return d


def _float8_from_text(s):
    t = s.strip(_WS)
    low = t.lower()
    if low in ("nan", "+nan", "-nan"):
        return float("nan")
    if low in ("infinity", "inf", "+infinity", "+inf"):
        return float("inf")
    if low in ("-infinity", "-inf"):
        return float("-inf")
    if not _NUMERIC_TEXT_RE.fullmatch(t):
        raise CastError('invalid input syntax for type double precision: "%s"' % s)
    f = float(t)
    if math.isinf(f):  # overflow (underflow to 0/denormal is accepted)
        raise CastError('"%s" is out of range for type double precision' % t)
    return f


def _bool_from_text(s):
    t = s.strip(_WS).lower()
    matches = {v for w, v in _BOOL_WORDS if t and w.startswith(t)}
    if len(matches) == 1:
        return matches.pop()
    raise CastError('invalid input syntax for type boolean: "%s"' % s)


# ------------------------------------------------------------- casts to each type

def _to_int(value, src, dst):
    lo, hi = INT_RANGES[dst]
    if src == "text":
        return _int_from_text(value, dst)
    if src == "numeric":
        d = value
        if d.is_nan() or d.is_infinite():
            raise CastError("%s out of range" % SQL_NAMES[dst])
        with localcontext() as ctx:  # numeric rounds half away from zero
            ctx.prec = max(28, len(d.as_tuple().digits) + 4)
            v = int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    elif src == "float8":
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            raise CastError("%s out of range" % SQL_NAMES[dst])
        v = int(round(f))  # rint(): round half to even
    elif src == "bool":
        if dst != "int4":  # only boolean -> integer exists
            _cannot("bool", dst)
        v = 1 if value else 0
    elif src in INT_RANGES:
        v = int(value)
    else:
        _cannot(src, dst)
    if not lo <= v <= hi:
        raise CastError("%s out of range" % SQL_NAMES[dst])
    return v


def _to_numeric(value, src):
    if src == "text":
        return _numeric_from_text(value)
    if src == "float8":
        f = float(value)
        if math.isnan(f):
            return Decimal("NaN")
        if math.isinf(f):
            return Decimal("Infinity") if f > 0 else Decimal("-Infinity")
        return Decimal("%.15g" % f)  # float8_numeric keeps DBL_DIG digits
    if src == "bool":
        _cannot("bool", "numeric")
    if src in INT_RANGES:
        return Decimal(int(value))
    return value  # numeric -> numeric


def _to_float8(value, src):
    if src == "text":
        return _float8_from_text(value)
    if src == "numeric":
        d = value
        if d.is_nan():
            return float("nan")
        if d == Decimal("Infinity"):
            return float("inf")
        if d == Decimal("-Infinity"):
            return float("-inf")
        s = _numeric_out(d)
        f = float(s)
        if math.isinf(f):
            raise CastError('"%s" is out of range for type double precision' % s)
        return f
    if src == "bool":
        _cannot("bool", "float8")
    if src in INT_RANGES:
        return float(int(value))
    return float(value)


def _to_bool(value, src):
    if src == "text":
        return _bool_from_text(value)
    if src == "int4":  # only integer -> boolean exists
        return int(value) != 0
    _cannot(src, "bool")


def _to_text(value, src):
    if src == "text":
        return str(value)
    if src == "bool":
        return "t" if value else "f"
    if src == "float8":
        return _float8_out(float(value))
    if src == "numeric":
        return _numeric_out(value)
    if src in INT_RANGES:
        return str(int(value))
    return str(value)


def _to_numeric_typmod(value, src, p, s):
    if not 1 <= p <= 1000:
        raise CastError("NUMERIC precision %d must be between 1 and 1000" % p)
    if not -1000 <= s <= 1000:
        raise CastError("NUMERIC scale %d must be between -1000 and 1000" % s)
    d = _to_numeric(value, src)
    if d.is_nan() or d.is_infinite():
        return d
    with localcontext() as ctx:  # numeric rounds half away from zero
        ctx.prec = max(28, max(d.adjusted(), 0) + max(s, 0) + 4)
        q = d.quantize(Decimal(1).scaleb(-s), rounding=ROUND_HALF_UP)
    if q != 0 and q.copy_abs().adjusted() >= p - s:
        raise CastError("numeric field overflow")
    return q


def _to_varchar(value, src, n):
    if n is not None:
        if n < 1:
            raise CastError("length for type varchar must be at least 1")
        if n > 10485760:
            raise CastError("length for type varchar cannot exceed 10485760")
    t = _to_text(value, src)
    return t if n is None else t[:n]


def _to_char(value, src, n):
    if n < 1:
        raise CastError("length for type bpchar must be at least 1")
    if n > 10485760:
        raise CastError("length for type bpchar cannot exceed 10485760")
    t = _to_text(value, src)[:n]
    return t + " " * (n - len(t))  # blank-padded


def cast(value, src, dst):
    if value is None:
        return None
    src = normalize_type(src)
    dst = normalize_type(dst)
    m = _TYPMOD_RE.fullmatch(dst)
    if m:
        kind, a, b = m.group(1), int(m.group(2)), m.group(3)
        if kind == "numeric":
            return _to_numeric_typmod(value, src, a, int(b) if b is not None else 0)
        if kind == "varchar":
            return _to_varchar(value, src, a)
        return _to_char(value, src, a)
    if dst in INT_RANGES:
        return _to_int(value, src, dst)
    if dst == "numeric":
        return _to_numeric(value, src)
    if dst == "float8":
        return _to_float8(value, src)
    if dst == "bool":
        return _to_bool(value, src)
    if dst == "text":
        return _to_text(value, src)
    if dst == "varchar":
        return _to_varchar(value, src, None)
    if dst == "char":
        return _to_char(value, src, 1)
    _cannot(src, dst)


# ------------------------------------------------------------- psql text output

def _numeric_out(d):
    if d.is_nan():
        return "NaN"
    if d == Decimal("Infinity"):
        return "Infinity"
    if d == Decimal("-Infinity"):
        return "-Infinity"
    return format(d, "f")  # numeric_out never uses scientific notation


def _float8_out(x):
    """Shortest round-trip output, formatted the way psql prints float8."""
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "-0" if math.copysign(1.0, x) < 0 else "0"
    sign = "-" if x < 0 else ""
    r = repr(abs(x))  # shortest round-trip digits, same as PostgreSQL's
    if "e" in r:
        mant, _, e = r.partition("e")
        e = int(e)
    else:
        mant, e = r, 0
    ip, _, fp = mant.partition(".")
    digits = (ip + fp).lstrip("0")
    lead = len(ip) + len(fp) - len(digits)
    digits = digits.rstrip("0")
    exp10 = e + len(ip) - lead - 1  # value = digits[0].digits[1:] * 10**exp10
    if -4 <= exp10 < 15:  # fixed notation
        if exp10 >= len(digits) - 1:
            out = digits + "0" * (exp10 - len(digits) + 1)
        elif exp10 >= 0:
            out = digits[:exp10 + 1] + "." + digits[exp10 + 1:]
        else:
            out = "0." + "0" * (-exp10 - 1) + digits
    else:  # scientific notation
        out = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        out += "e%+03d" % exp10
    return sign + out


def text_out(value, typ):
    """How psql shows a value of the given type."""
    if value is None:
        return None
    typ = normalize_type(typ).split("(")[0]
    if typ == "bool":
        return "t" if value else "f"
    if typ == "float8":
        return _float8_out(float(value))
    if typ == "numeric":
        return _numeric_out(value if isinstance(value, Decimal) else Decimal(str(value)))
    return str(value)
