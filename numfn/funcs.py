"""Numeric and integer functions, aligned with PostgreSQL.

Arguments carry their SQL type: ["numeric", "2.5"], ["float8", 2.5], ["int", 7].
Results are returned as (type, value); numeric values are Decimal and keep the
same scale (trailing zeroes) that PostgreSQL would show in psql.
"""
import math
from decimal import (Decimal, ROUND_HALF_UP, ROUND_DOWN, localcontext,
                     getcontext, InvalidOperation)

# Keep PostgreSQL's default numeric precision (28 significant digits) for
# ordinary arithmetic; transcendental functions raise it locally.
getcontext().prec = 28

_NUMERIC_MIN_SIG_DIGITS = 16
_TRANSCENDENTAL_PREC = 100


class NumericError(Exception):
    pass


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _dec(v):
    return v if isinstance(v, Decimal) else Decimal(v)


def _check_scale(scale):
    if not (-1000 <= scale <= 1000):
        raise NumericError("numeric field overflow")


def _quantize(v, scale, rounding):
    """Quantize to *scale* decimal places; scale may be negative."""
    step = Decimal(1).scaleb(-scale)  # scale=-2 -> 1E+2, scale=2 -> 0.01
    try:
        return v.quantize(step, rounding=rounding)
    except InvalidOperation:
        raise NumericError("numeric field overflow")


def _trunc_div(a, b):
    """Quotient truncated toward zero (PG integer /,% and numeric div).

    Works for both ints and Decimals; Python's // floors, PG truncates.
    """
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _weight(d):
    """PostgreSQL numeric weight: exponent of the most significant digit."""
    if not d:
        return 0
    sig = len(d.as_tuple().digits)
    return d.as_tuple().exponent + sig - 1


def _dscale_sqrt(v):
    # numeric_sqrt: rscale = max(MIN_SIG_DIGITS, sig_digits(v) - 1)
    sig = 0 if not v else len(v.as_tuple().digits)
    return max(_NUMERIC_MIN_SIG_DIGITS, sig - 1)


# --------------------------------------------------------------------------- #
# rounding / truncation
# --------------------------------------------------------------------------- #
def round_(x, scale=0):
    t, v = x
    _check_scale(scale)
    if t == "float8":
        # round(double precision) -> double precision, ties to even.
        return "float8", float(round(v)) if scale == 0 else float(round(v, scale))
    d = _dec(v)
    return "numeric", _quantize(d, scale, ROUND_HALF_UP)


def trunc(x, scale=0):
    t, v = x
    _check_scale(scale)
    if t == "float8":
        # trunc(double precision) -> double precision, toward zero.
        if scale == 0:
            return "float8", math.trunc(v) + 0.0
        return "float8", float(_quantize(Decimal(v), scale, ROUND_DOWN))
    d = _dec(v)
    return "numeric", _quantize(d, scale, ROUND_DOWN)


def ceil_(x):
    t, v = x
    if t == "float8":
        return "float8", float(math.ceil(v))
    return "numeric", Decimal(math.ceil(_dec(v)))


def floor_(x):
    t, v = x
    if t == "float8":
        return "float8", float(math.floor(v))
    return "numeric", Decimal(math.floor(_dec(v)))


# --------------------------------------------------------------------------- #
# division / modulo
# --------------------------------------------------------------------------- #
def div(a, b):
    # div(numeric, numeric) -> numeric; there is no double precision version.
    if a[0] != "numeric" or b[0] != "numeric":
        raise NumericError(
            "function div(%s, %s) does not exist"
            % ("double precision" if a[0] == "float8" else a[0],
               "double precision" if b[0] == "float8" else b[0]))
    bd = _dec(b[1])
    if bd == 0:
        raise NumericError("division by zero")
    return "numeric", _dec(a[1]) // bd


def mod(a, b):
    t = a[0]
    if t == "int":
        av, bv = a[1], b[1]
        if bv == 0:
            raise NumericError("division by zero")
        r = abs(av) % abs(bv)
        return "int", r if av >= 0 else -r
    if t == "float8":
        if b[1] == 0:
            # PG returns NaN rather than raising for double precision %.
            return "float8", float("nan")
        return "float8", math.fmod(a[1], b[1])
    ad, bd = _dec(a[1]), _dec(b[1])
    if bd == 0:
        raise NumericError("division by zero")
    return "numeric", ad - bd * (ad // bd)


def int_divide(a, b):
    """a / b on integers: truncate toward zero (PG), not floor (Python)."""
    if b[1] == 0:
        raise NumericError("division by zero")
    return "int", _trunc_div(a[1], b[1])


# --------------------------------------------------------------------------- #
# transcendental family
# --------------------------------------------------------------------------- #
def log(x, base=None):
    t, v = x
    if t == "float8":
        # log(double precision) is base 10; no two-arg double precision form.
        if base is not None:
            raise NumericError("function log(double precision, double precision) does not exist")
        if v <= 0:
            raise NumericError("cannot take logarithm of a negative number" if v < 0
                               else "cannot take logarithm of zero")
        return "float8", math.log10(v)

    d = _dec(v)
    with localcontext() as ctx:
        ctx.prec = _TRANSCENDENTAL_PREC
        if base is None:
            if d <= 0:
                raise NumericError("cannot take logarithm of a negative number" if d < 0
                                   else "cannot take logarithm of zero")
            r = d.log10()
            dscale = _NUMERIC_MIN_SIG_DIGITS
        else:
            bd = _dec(base[1])
            if d <= 0 or bd <= 0 or bd == 1:
                raise NumericError("cannot take logarithm of a non-positive number")
            r = d.ln() / bd.ln()
            dscale = _NUMERIC_MIN_SIG_DIGITS
    return "numeric", _quantize(r, dscale, ROUND_HALF_UP)


def sqrt_(x):
    t, v = x
    if t == "float8":
        if v < 0:
            raise NumericError("cannot take square root of a negative number")
        return "float8", math.sqrt(v)
    d = _dec(v)
    if d < 0:
        raise NumericError("cannot take square root of a negative number")
    with localcontext() as ctx:
        ctx.prec = _TRANSCENDENTAL_PREC
        r = d.sqrt()
    return "numeric", _quantize(r, _dscale_sqrt(d), ROUND_HALF_UP)


def power(a, b):
    if a[0] == "float8" or b[0] == "float8":
        av, bv = float(a[1]), float(b[1])
        try:
            return "float8", av ** bv
        except OverflowError:
            return "float8", float("inf")
    ad, bd = _dec(a[1]), _dec(b[1])
    if ad == 0 and bd < 0:
        raise NumericError("division by zero")  # PG: 0 to a negative power
    # power(numeric, numeric): integer exponents are computed exactly.
    if bd == bd.to_integral_value():
        if ad == 0 and bd == 0:
            return "numeric", Decimal(1)  # PostgreSQL: 0 ^ 0 = 1
        try:
            return "numeric", ad ** int(bd)
        except InvalidOperation:
            raise NumericError("invalid input for power function")
    if ad < 0:
        raise NumericError("invalid input for power function: negative value with non-integer exponent")
    try:
        with localcontext() as ctx:
            ctx.prec = _TRANSCENDENTAL_PREC
            r = (ad.ln() * bd).exp()
            # numeric power's approximate path guarantees at least
            # MIN_SIG_DIGITS significant digits, i.e. dscale = 16 - weight(r).
            dscale = max(0, _NUMERIC_MIN_SIG_DIGITS - _weight(r))
        return "numeric", _quantize(r, dscale, ROUND_HALF_UP)
    except InvalidOperation:
        raise NumericError("invalid input for power function")


def sign(x):
    t, v = x
    if t == "float8":
        return "float8", (v > 0) - (v < 0) + 0.0
    d = _dec(v)
    return "numeric", Decimal((d > 0) - (d < 0))


# --------------------------------------------------------------------------- #
# width_bucket
# --------------------------------------------------------------------------- #
def width_bucket(x, lo, hi, n):
    count = n[1] if isinstance(n, (list, tuple)) else n
    if x[0] == "float8":
        operand, low, high = float(x[1]), float(lo[1]), float(hi[1])
        if count <= 0.0:
            raise NumericError("width_bucket bucket count must be positive")
        if math.isnan(operand):
            raise NumericError("NaN, infinity, or overflow not allowed in width_bucket")
        if low == high:
            raise NumericError("width_bucket cannot accept zero width buckets")
        # trunc toward zero so reversed bounds (high < low) bucket correctly
        result = math.trunc((operand - low) / (high - low) * count) + 1
        if result < 0:
            result = 0.0
        if result > count + 1:
            result = float(count + 1)
        return "int", int(result)

    operand = _dec(x[1])
    low = _dec(lo[1])
    high = _dec(hi[1])
    if count <= 0:
        raise NumericError("width_bucket bucket count must be positive")
    if operand.is_nan():
        raise NumericError("NaN, infinity, or overflow not allowed in width_bucket")
    if low == high:
        raise NumericError("width_bucket cannot accept zero width buckets")
    with localcontext() as ctx:
        ctx.prec = _TRANSCENDENTAL_PREC
        result = _trunc_div((operand - low) * count, high - low) + 1
    if result < 0:
        result = Decimal(0)
    if result > count + 1:
        result = Decimal(count + 1)
    return "int", int(result)


# --------------------------------------------------------------------------- #
# gcd / lcm (integers only)
# --------------------------------------------------------------------------- #
def gcd(a, b):
    if a[0] != "int" or b[0] != "int":
        raise NumericError("function gcd(numeric, numeric) does not exist")
    return "int", math.gcd(a[1], b[1])


def lcm(a, b):
    if a[0] != "int" or b[0] != "int":
        raise NumericError("function lcm(numeric, numeric) does not exist")
    av, bv = a[1], b[1]
    g = math.gcd(av, bv)
    if g == 0:
        return "int", 0
    r = abs(av // g) * abs(bv)
    if r > 9223372036854775807:
        raise NumericError("bigint out of range")
    return "int", r


# --------------------------------------------------------------------------- #
# rendering: exact text psql would print
# --------------------------------------------------------------------------- #
def _format_numeric(d):
    d = _dec(d)
    if d.is_nan():
        return "NaN"
    if d.is_infinite():
        return "Infinity" if d > 0 else "-Infinity"
    sign, digits, exp = d.as_tuple()
    if all(x == 0 for x in digits):
        # A plain numeric zero (exponent >= 0) prints as "0"; zeroes produced
        # by quantize() to fractional scales keep that scale, e.g.
        # round(0, 3) -> 0.000. Positive exponents carry no visible digits.
        if exp >= 0:
            return "0"
        sign = 0
    s = "".join(str(x) for x in digits)
    if exp >= 0:
        s += "0" * exp
    else:
        pos = len(s) + exp
        if pos <= 0:
            s = "0." + ("0" * (-pos)) + s
        else:
            s = s[:pos] + "." + s[pos:]
    if sign:
        s = "-" + s
    return s


def _format_float8(f):
    if math.isnan(f):
        return "NaN"
    if math.isinf(f):
        return "Infinity" if f > 0 else "-Infinity"
    if f == 0.0:  # -0.0 prints as 0 in PostgreSQL
        return "0"
    if f == int(f) and abs(f) < 1e16:  # psql shows whole float8 without a dot
        return str(int(f))
    return repr(f)


def render(result):
    t, v = result
    if t == "numeric":
        return _format_numeric(v)
    if t == "float8":
        return _format_float8(v)
    return str(v)

