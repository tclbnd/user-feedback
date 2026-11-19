"""Numeric and integer functions (PostgreSQL names).

Arguments carry their SQL type: ["numeric", "2.5"], ["float8", 2.5], ["int", 7].
Results are returned as (type, value); numeric values are Decimal.
"""
import math
from decimal import Decimal


class NumericError(Exception):
    pass


def round_(x, scale=0):
    t, v = x
    if t == "float8":
        return "float8", float(round(v, scale))
    return "numeric", round(Decimal(v), scale)


def trunc(x, scale=0):
    t, v = x
    q = Decimal(1).scaleb(-scale) if scale >= 0 else Decimal(1)
    return "numeric", Decimal(v).quantize(q, rounding="ROUND_DOWN")


def ceil_(x):
    return x[0], math.ceil(Decimal(x[1]))


def floor_(x):
    return x[0], math.floor(Decimal(x[1]))


def div(a, b):
    return "numeric", Decimal(a[1]) // Decimal(b[1])


def mod(a, b):
    return a[0], int(a[1]) % int(b[1]) if a[0] == "int" else Decimal(a[1]) % Decimal(b[1])


def int_divide(a, b):
    """a / b on integers"""
    if b[1] == 0:
        raise NumericError("division by zero")
    return "int", a[1] // b[1]


def log(x, base=None):
    v = Decimal(x[1])
    if base is None:
        return "numeric", Decimal(math.log(v))
    return "numeric", Decimal(math.log(v, Decimal(base[1])))


def sqrt_(x):
    return x[0], Decimal(math.sqrt(Decimal(x[1])))


def power(a, b):
    return "numeric", Decimal(a[1]) ** Decimal(b[1])


def sign(x):
    v = Decimal(x[1])
    return x[0], (v > 0) - (v < 0)


def width_bucket(x, lo, hi, n):
    v, lo, hi = Decimal(x[1]), Decimal(lo[1]), Decimal(hi[1])
    return "int", int((v - lo) / (hi - lo) * n) + 1


def gcd(a, b):
    return "int", math.gcd(a[1], b[1])


def lcm(a, b):
    return "int", abs(a[1] * b[1]) // math.gcd(a[1], b[1])
