"""Numeric functions and operators of the query engine (PostgreSQL names and behaviour).

Arguments come from the SQL layer as literals: a JSON integer is an integer
literal, a string such as "2.50" is a numeric literal, null is NULL.
Results are returned as text, the way psql prints them.
"""
import math


class FunctionError(Exception):
    pass


def val(v):
    if v is None or isinstance(v, int):
        return v
    return float(v)


def show(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def strict(fn):
    def wrapper(*args):
        if any(a is None for a in args):
            return None
        return show(fn(*[val(a) for a in args]))
    wrapper.__name__ = fn.__name__
    return wrapper


@strict
def add(a, b):
    return a + b


@strict
def sub(a, b):
    return a - b


@strict
def mul(a, b):
    return a * b


@strict
def divide(a, b):
    return a / b


@strict
def modulo(a, b):
    return a % b


@strict
def abs_(x):
    return abs(x)


@strict
def sign(x):
    return (x > 0) - (x < 0)


@strict
def round_(x, s=0):
    return round(x, int(s))


@strict
def trunc(x, s=0):
    f = 10 ** int(s)
    return math.trunc(x * f) / f


@strict
def ceil(x):
    return math.ceil(x)


@strict
def floor(x):
    return math.floor(x)


@strict
def div(a, b):
    return a // b


@strict
def mod(a, b):
    return a % b


@strict
def sqrt(x):
    return math.sqrt(x)


@strict
def ln(x):
    return math.log(x)


@strict
def log(a, b=None):
    if b is None:
        return math.log10(a)
    return math.log(b, a)


@strict
def gcd(a, b):
    return math.gcd(int(a), int(b))


@strict
def lcm(a, b):
    return a * b // math.gcd(int(a), int(b)) if a and b else 0


@strict
def factorial(n):
    return math.factorial(int(n))


@strict
def width_bucket(x, low, high, count):
    return int((x - low) / (high - low) * count) + 1


@strict
def scale(x):
    s = repr(float(x))
    return len(s.split(".")[1]) if "." in s and not s.endswith(".0") else 0


@strict
def min_scale(x):
    frac = repr(float(x)).split(".")[1].rstrip("0")
    return len(frac)


@strict
def trim_scale(x):
    return x
