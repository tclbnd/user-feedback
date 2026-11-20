"""Numeric functions and operators of the query engine (PostgreSQL names and behaviour).

Arguments come from the SQL layer as literals: a JSON integer is an integer
literal, a string such as "2.50" is a numeric literal, null is NULL.
Results are returned as text, the way psql prints them.

PostgreSQL arithmetic is exact decimal arithmetic (never binary floating
point), so this module is built on :class:`decimal.Decimal` and Python ``int``.
The weight/scale rules that decide how many fractional digits a result keeps
(and therefore its text form) follow PostgreSQL's numeric.c.
"""
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    localcontext,
)
import math

# PostgreSQL numeric maximum precision (digits), matching NUMERIC_MAX_PRECISION.
MAX_PRECISION = 1000

# Internal working precision for Decimal operations.  Inputs can carry up to
# 1000 digits; this keeps headroom for division, roots and logarithms.
_WORK_PRECISION = 2000


class FunctionError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Literal handling and text output
# --------------------------------------------------------------------------- #

def val(v):
    """Promote a literal: JSON integer -> int, string numeric -> Decimal."""
    if v is None or isinstance(v, int):
        return v
    try:
        return Decimal(str(v))
    except InvalidOperation:
        raise FunctionError('invalid input syntax for type numeric: "%s"' % v)


def dec(v):
    """Promote an integer literal to an exact scale-0 Decimal."""
    return v if isinstance(v, Decimal) else Decimal(v)


def dscale(d):
    """Number of fractional digits (PostgreSQL ``dscale``); >= 0."""
    exp = d.as_tuple().exponent
    return -exp if exp < 0 else 0


def weight(d):
    """PostgreSQL ``weight`` of the most significant digit (10**weight)."""
    sig = len(d.as_tuple().digits)
    exp = d.as_tuple().exponent
    return sig + exp - 1


def _fmt(d):
    """Fixed-point text, exactly the digits PostgreSQL would print."""
    sign, digits, exp = d.as_tuple()
    # PostgreSQL keeps a negative zero's sign.
    neg = "-" if sign else ""
    s = "".join(map(str, digits))
    if exp >= 0:
        return neg + s + "0" * exp
    point = len(s) + exp
    if point <= 0:
        return neg + "0." + "0" * (-point) + s
    return neg + s[:point] + "." + s[point:]


def show(v):
    """Render a result the way psql prints it (no scientific notation)."""
    if v is None:
        return None
    if isinstance(v, int):
        return str(v)
    return _fmt(v)


def strict(fn):
    def wrapper(*args):
        if any(a is None for a in args):
            return None
        return show(fn(*[val(a) for a in args]))
    wrapper.__name__ = fn.__name__
    return wrapper


def _check_precision(d):
    """Raise the numeric field overflow error if |d| has more than 1000 digits."""
    if d and len(d.as_tuple().digits) > MAX_PRECISION:
        raise FunctionError("numeric field overflow")
    return d


def _numeric_op(fn):
    """Run a numeric (Decimal) operation at the internal working precision."""
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        return _check_precision(fn())


# --------------------------------------------------------------------------- #
# Arithmetic operators
# --------------------------------------------------------------------------- #

@strict
def add(a, b):
    if isinstance(a, int) and isinstance(b, int):
        return a + b
    # numeric add: result scale is the maximum of the operand scales.
    return _numeric_op(lambda: dec(a) + dec(b))


@strict
def sub(a, b):
    if isinstance(a, int) and isinstance(b, int):
        return a - b
    return _numeric_op(lambda: dec(a) - dec(b))


@strict
def mul(a, b):
    if isinstance(a, int) and isinstance(b, int):
        return a * b
    # numeric mul: result scale is the sum of the operand scales.
    return _numeric_op(lambda: dec(a) * dec(b))


def _trunc_div(x, y):
    """Integer-style quotient truncated toward zero."""
    q = abs(x) // abs(y)
    return -q if (x < 0) != (y < 0) else q


def _pad_to(d, keep):
    """Pad fractional zeros so the value keeps at least ``keep`` frac digits."""
    if keep <= dscale(d):
        return d
    return d.quantize(Decimal(1).scaleb(-keep))


@strict
def divide(a, b):
    if isinstance(a, int) and isinstance(b, int):
        if b == 0:
            raise FunctionError("division by zero")
        return _trunc_div(a, b)
    x, y = dec(a), dec(b)
    if y == 0:
        raise FunctionError("division by zero")
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        # PG15+ rule: non-terminating quotients carry at least 16 significant
        # digits, and numeric division always keeps >= 1 fractional digit.
        rscale = max(1, 16 + weight(y) - weight(x))
        raw = x / y
        if _is_exact(x, y):
            # A terminating quotient keeps its natural digits, padded back to
            # the smallest operand scale it cannot legitimately drop.
            return _pad_to(raw.normalize(), max(dscale(x), dscale(y)))
        return raw.quantize(Decimal(1).scaleb(-rscale), rounding=ROUND_HALF_UP)


def _is_exact(x, y):
    """True if x/y is a terminating decimal (reduced denominator is 2^a*5^b)."""
    num = int(x.scaleb(dscale(x)))
    den = int(y.scaleb(dscale(y)))
    if num == 0:
        return True
    g = math.gcd(abs(num), abs(den))
    r = abs(den) // g
    while r % 2 == 0:
        r //= 2
    while r % 5 == 0:
        r //= 5
    return r == 1


@strict
def modulo(a, b):
    if isinstance(a, int) and isinstance(b, int):
        if b == 0:
            raise FunctionError("division by zero")
        # Truncated modulo: result takes the sign of the dividend.
        return a - _trunc_div(a, b) * b
    y = dec(b)
    if y == 0:
        raise FunctionError("division by zero")
    # numeric mod: result scale is the maximum of the operand scales.
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        return dec(a) % y


# --------------------------------------------------------------------------- #
# Sign and rounding functions
# --------------------------------------------------------------------------- #

@strict
def abs_(x):
    return abs(x)


@strict
def sign(x):
    if isinstance(x, int) or x == 0:
        return (x > 0) - (x < 0)
    return Decimal((x > 0) - (x < 0))


@strict
def round_(x, s=0):
    s = int(s)
    if s < -MAX_PRECISION or s > MAX_PRECISION:
        raise FunctionError("requested value length exceeds maximum output length")
    d = dec(x)
    if s < 0:
        # Rounding at a position left of the decimal point yields scale 0.
        return d.quantize(Decimal(1).scaleb(-s), rounding=ROUND_HALF_UP).quantize(Decimal(1))
    # At scale s >= 0 the result keeps exactly s fractional digits.
    return d.quantize(Decimal(1).scaleb(-s), rounding=ROUND_HALF_UP)


@strict
def trunc(x, s=0):
    s = int(s)
    if s < -MAX_PRECISION or s > MAX_PRECISION:
        raise FunctionError("requested value length exceeds maximum output length")
    d = dec(x)
    if s < 0:
        return d.quantize(Decimal(1).scaleb(-s), rounding=ROUND_DOWN).quantize(Decimal(1))
    return d.quantize(Decimal(1).scaleb(-s), rounding=ROUND_DOWN)


@strict
def ceil(x):
    if isinstance(x, int):
        return x
    return x.to_integral_value(rounding=ROUND_CEILING)


@strict
def floor(x):
    if isinstance(x, int):
        return x
    return x.to_integral_value(rounding=ROUND_FLOOR)


# --------------------------------------------------------------------------- #
# Integer division (the div(x,y) function; % / mod use ``modulo`` above)
# --------------------------------------------------------------------------- #

@strict
def div(a, b):
    if isinstance(a, int) and isinstance(b, int):
        if b == 0:
            raise FunctionError("division by zero")
        return _trunc_div(a, b)
    y = dec(b)
    if y == 0:
        raise FunctionError("division by zero")
    # numeric div truncates toward zero and returns scale 0.
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        return (dec(a) / y).to_integral_value(rounding=ROUND_DOWN)


# --------------------------------------------------------------------------- #
# Roots and logarithms
# --------------------------------------------------------------------------- #

@strict
def sqrt(x):
    if isinstance(x, int):
        if x < 0:
            raise FunctionError("cannot take square root of a negative number")
        d = Decimal(x)
    else:
        if x < 0:
            raise FunctionError("cannot take square root of a negative number")
        d = x
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        rscale = max(16, dscale(d))
        return d.sqrt().quantize(Decimal(1).scaleb(-rscale), rounding=ROUND_HALF_UP)


def _exp_small(x):
    """exp(x) for |x| tiny, via the Taylor series."""
    term = Decimal(1)
    total = Decimal(1)
    n = 1
    target = Decimal(1).scaleb(-_WORK_PRECISION + 6)
    while abs(term) >= target and n < 10000:
        term *= x / n
        total += term
        n += 1
    return total


def _ln(d):
    """Natural logarithm at high working precision.

    A double-precision seed from :func:`math.log` is corrected with one
    Newton step (ln d = a + ln(d/e**a)); that removes the ~1e-16 seed
    error, leaving a result accurate to the full working precision.
    """
    if d <= 0:
        raise FunctionError("cannot take logarithm of a negative number"
                            if d < 0 else
                            "cannot take logarithm of zero")
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        # math needs an actual float; inputs beyond float range are reduced
        # via ln(d) = ln(m) + adj*ln(10), 1 <= m < 10.
        adj = d.adjusted()
        m = d.scaleb(-adj)
        seed = Decimal(repr(math.log(float(m))))
        # Newton: ln(m) = seed + ln(m / exp(seed)); the residual z is ~1.
        z = m / _exp_small(seed)
        corrected = seed + _ln_small(z)
        return corrected + adj * _LN10


def _ln_small(z):
    """ln(z) for z very close to 1, via the atanh series."""
    y = (z - 1) / (z + 1)
    y2 = y * y
    term = y
    total = Decimal(0)
    n = 1
    target = Decimal(1).scaleb(-_WORK_PRECISION + 6)
    while abs(term) >= target and n < 10000:
        total += term / n
        term *= y2
        n += 2
    return 2 * total


with localcontext() as _ctx:
    _ctx.prec = _WORK_PRECISION
    # ln(10): double seed plus one Newton correction, computed once.
    _seed10 = Decimal(repr(math.log(10.0)))
    _LN10 = _seed10 + _ln_small(Decimal(10) / _exp_small(_seed10))


@strict
def ln(x):
    d = dec(x)
    rscale = max(16, dscale(d))
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        return _ln(d).quantize(Decimal(1).scaleb(-rscale),
                               rounding=ROUND_HALF_UP)


@strict
def log(a, b=None):
    if b is None:
        d = dec(a)
        rscale = max(16, dscale(d))
        if d <= 0:
            raise FunctionError("cannot take logarithm of a negative number"
                                if d < 0 else
                                "cannot take logarithm of zero")
        with localcontext() as ctx:
            ctx.prec = _WORK_PRECISION
            return (_ln(d) / _ln(Decimal(10))).quantize(
                Decimal(1).scaleb(-rscale), rounding=ROUND_HALF_UP)
    base, arg = dec(a), dec(b)
    rscale = max(16, dscale(base), dscale(arg))
    if base <= 0:
        raise FunctionError("cannot take logarithm of a negative number"
                            if base < 0 else
                            "cannot take logarithm of zero")
    if base == 1:
        raise FunctionError("invalid argument for logarithm")
    if arg <= 0:
        raise FunctionError("cannot take logarithm of a negative number"
                            if arg < 0 else
                            "cannot take logarithm of zero")
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        return (_ln(arg) / _ln(base)).quantize(
            Decimal(1).scaleb(-rscale), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# gcd / lcm / factorial
# --------------------------------------------------------------------------- #

@strict
def gcd(a, b):
    x, y = dec(a), dec(b)
    rscale = max(dscale(x), dscale(y))
    # Euclid on the integer coefficients at the common scale.
    xa = int(x.scaleb(dscale(x)))
    ya = int(y.scaleb(dscale(y)))
    g_int = math.gcd(abs(xa), abs(ya))
    d = Decimal(g_int).scaleb(-rscale)
    return d.quantize(Decimal(1)) if rscale == 0 else d


@strict
def lcm(a, b):
    x, y = dec(a), dec(b)
    rscale = max(dscale(x), dscale(y))
    if x == 0 or y == 0:
        d = Decimal(0)
        return d if rscale == 0 else d.quantize(Decimal(1).scaleb(-rscale))
    xa = int(x.scaleb(dscale(x)))
    ya = int(y.scaleb(dscale(y)))
    g = math.gcd(abs(xa), abs(ya))
    value = abs(xa // g) * abs(ya)
    d = Decimal(value).scaleb(-rscale)
    return d.quantize(Decimal(1)) if rscale == 0 else d


@strict
def factorial(n):
    x = int(dec(n))
    if x < 0:
        raise FunctionError("factorial of a negative number is not defined")
    if x > MAX_PRECISION:
        raise FunctionError("factorial of a number greater than one thousand is not defined")
    return math.factorial(x)


# --------------------------------------------------------------------------- #
# width_bucket
# --------------------------------------------------------------------------- #

@strict
def width_bucket(x, low, high, count):
    c = int(count)
    if c <= 0:
        raise FunctionError("width_bucket count must be greater than zero")
    bound1, bound2, operand = dec(low), dec(high), dec(x)
    if bound1 == bound2:
        raise FunctionError("width_bucket lower bound must not equal upper bound")
    if operand < min(bound1, bound2):
        return 0
    if operand >= max(bound1, bound2):
        return c + 1
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        bucket = ((operand - bound1) * c / (bound2 - bound1)).to_integral_value(
            rounding=ROUND_FLOOR)
        return int(bucket) + 1


# --------------------------------------------------------------------------- #
# scale functions
# --------------------------------------------------------------------------- #

@strict
def scale(x):
    if isinstance(x, int):
        return 0
    return dscale(x)


@strict
def min_scale(x):
    if isinstance(x, int):
        return 0
    n = x.normalize()
    if n == 0:
        return 0
    exp = n.as_tuple().exponent
    return -exp if exp < 0 else 0


@strict
def trim_scale(x):
    if isinstance(x, int):
        return x
    return x.normalize()
