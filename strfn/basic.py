"""String functions of the query engine (PostgreSQL names and behaviour).

All functions take Python values; NULL is None.  Unless stated otherwise a
NULL argument gives a NULL result (the functions are STRICT).

Error cases that PostgreSQL rejects raise FunctionError so the SQL layer can
report them instead of producing a value (or an unrelated Python exception).
"""

import re


class FunctionError(Exception):
    pass


def strict(fn):
    def wrapper(*args):
        if any(a is None for a in args):
            return None
        return fn(*args)
    wrapper.__name__ = fn.__name__
    return wrapper


def _text(value):
    """Text representation of a non-NULL scalar, matching PostgreSQL casts."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _check_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise FunctionError("%s must be an integer" % name)
    return value


def concat(*args):
    # concat() is not strict: NULL arguments are ignored, never printed.
    return "".join("" if a is None else _text(a) for a in args)


def concat_ws(sep, *args):
    # NULL separator -> NULL; NULL arguments are skipped (no spare separators).
    if sep is None:
        return None
    return sep.join(_text(a) for a in args if a is not None)


def cat(a, b):
    """a || b (strict: NULL on either side gives NULL)."""
    if a is None or b is None:
        return None
    return _text(a) + _text(b)


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
    # PostgreSQL: first letter of each word upper-cased, the rest lower-cased;
    # a word starts after any non-letter character (digits, punctuation, ...).
    def up(match):
        word = match.group(0)
        return word[0].upper() + word[1:]

    return re.sub(r"[^\W\d_]+", up, s.lower())


@strict
def reverse(s):
    return s[::-1]


@strict
def repeat(s, n):
    _check_int(n, "number of times to repeat")
    # PostgreSQL treats a negative count as zero.
    return s * max(n, 0)


@strict
def left(s, n):
    _check_int(n, "number of characters")
    if n < 0:
        return s[:max(len(s) + n, 0)]
    return s[:n]


@strict
def right(s, n):
    _check_int(n, "number of characters")
    if n < 0:
        # PostgreSQL: all but the last |n| characters.
        return s[:max(len(s) + n, 0)]
    return s[len(s) - n:]


@strict
def substr(s, start, count=None):
    _check_int(start, "starting position")
    # PostgreSQL: a non-positive start clamps to 1 and the offset eats into
    # the requested length (e.g. substr('hello', -3, 6) = 'he').
    if start < 1:
        if count is not None:
            count -= 1 - start
        start = 1
    begin = start - 1
    if count is None:
        return s[begin:]
    _check_int(count, "length")
    if count < 0:
        raise FunctionError("negative substring length not allowed")
    return s[begin:begin + count]


@strict
def strpos(s, sub):
    # PostgreSQL positions are 1-based; a missing substring yields 0.
    return s.find(sub) + 1


@strict
def split_part(s, delim, n):
    _check_int(n, "field position")
    if n <= 0:
        raise FunctionError("field position must be greater than zero")
    if delim == "":
        raise FunctionError("field delimiter cannot be empty")
    parts = s.split(delim)
    if n > len(parts):
        # PostgreSQL returns an empty string for a field past the end.
        return ""
    return parts[n - 1]


@strict
def lpad(s, n, fill=" "):
    _check_int(n, "length")
    if n < 0:
        n = 0
    if len(s) >= n:
        # PostgreSQL truncates rather than returning the over-long input.
        return s[:n]
    need = n - len(s)
    if fill == "":
        raise FunctionError("lpad: length parameter exceeds input string length and padding string is empty")
    return (fill * ((need + len(fill) - 1) // len(fill)))[:need] + s


@strict
def rpad(s, n, fill=" "):
    _check_int(n, "length")
    if n < 0:
        n = 0
    if len(s) >= n:
        return s[:n]
    need = n - len(s)
    if fill == "":
        raise FunctionError("rpad: length parameter exceeds input string length and padding string is empty")
    return s + (fill * ((need + len(fill) - 1) // len(fill)))[:need]


@strict
def btrim(s, chars=" "):
    if chars == "":
        return s
    return s.strip(chars)


@strict
def ltrim(s, chars=" "):
    if chars == "":
        return s
    return s.lstrip(chars)


@strict
def rtrim(s, chars=" "):
    if chars == "":
        return s
    return s.rstrip(chars)


@strict
def replace(s, old, new):
    if old == "":
        # PostgreSQL replace() with an empty search string is a no-op.
        return s
    return s.replace(old, new)


@strict
def translate(s, frm, to):
    if frm == "":
        return s
    table = {}
    for i, ch in enumerate(frm):
        if ord(ch) in table:
            # PostgreSQL keeps the first mapping for a duplicated from-char.
            continue
        table[ord(ch)] = to[i] if i < len(to) else None
    return s.translate(table)


def _overlay(s, placing, start, count=None):
    _check_int(start, "starting position")
    if count is None:
        count = len(placing)
    _check_int(count, "number of characters to replace")
    if count < 0:
        raise FunctionError("negative number of characters to replace not allowed")
    begin = max(start - 1, 0)
    return s[:begin] + placing + s[begin + count:]

overlay = strict(_overlay)


@strict
def chr_(code):
    _check_int(code, "code")
    if code < 0:
        raise FunctionError("character number must be positive")
    if code > 0x10FFFF:
        raise FunctionError("requested character too large for encoding")
    return chr(code)


@strict
def ascii_(s):
    if s == "":
        raise FunctionError("string is empty")
    return ord(s[0])


@strict
def starts_with(s, prefix):
    return s.startswith(prefix)


@strict
def to_hex(n, typ="bigint"):
    _check_int(n, "integer")
    # to_hex operates on bigint; negative values print as 64-bit two's complement.
    return format(n & 0xFFFFFFFFFFFFFFFF, "x")
