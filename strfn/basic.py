"""String functions of the query engine (PostgreSQL names and behaviour).

All functions take Python values; NULL is None.  Unless stated otherwise a
NULL argument gives a NULL result (the functions are STRICT).
"""


class FunctionError(Exception):
    pass


def strict(fn):
    def wrapper(*args):
        if any(a is None for a in args):
            return None
        return fn(*args)
    wrapper.__name__ = fn.__name__
    return wrapper


def concat(*args):
    return "".join(str(a) for a in args)


def concat_ws(sep, *args):
    if sep is None:
        return None
    return sep.join(str(a) for a in args)


def cat(a, b):
    """a || b"""
    if a is None or b is None:
        return None
    return str(a) + str(b)


@strict
def length(s):
    return len(s)


@strict
def octet_length(s):
    return len(s.encode("utf-8"))


@strict
def upper(s):
    return s.upper()


@strict
def lower(s):
    return s.lower()


@strict
def initcap(s):
    return s.title()


@strict
def reverse(s):
    return s[::-1]


@strict
def repeat(s, n):
    return s * n


@strict
def left(s, n):
    return s[:n]


@strict
def right(s, n):
    return s[len(s) - n:]


@strict
def substr(s, start, count=None):
    begin = start - 1
    if count is None:
        return s[begin:]
    return s[begin:begin + count]


@strict
def strpos(s, sub):
    return s.find(sub) + 1


@strict
def split_part(s, delim, n):
    return s.split(delim)[n - 1]


@strict
def lpad(s, n, fill=" "):
    return s.rjust(n, fill[0])


@strict
def rpad(s, n, fill=" "):
    return s.ljust(n, fill[0])


@strict
def btrim(s, chars=" "):
    return s.strip(chars)


@strict
def ltrim(s, chars=" "):
    return s.lstrip(chars)


@strict
def rtrim(s, chars=" "):
    return s.rstrip(chars)


@strict
def replace(s, old, new):
    return s.replace(old, new)


@strict
def translate(s, frm, to):
    table = {ord(a): b for a, b in zip(frm, to)}
    return s.translate(table)


@strict
def overlay(s, placing, start, count=None):
    count = len(placing) if count is None else count
    return s[:start - 1] + placing + s[start - 1 + count:]


@strict
def chr_(code):
    return chr(code)


@strict
def ascii_(s):
    return ord(s[0])


@strict
def starts_with(s, prefix):
    return s.startswith(prefix)


@strict
def to_hex(n, typ="int"):
    return hex(n)[2:]
