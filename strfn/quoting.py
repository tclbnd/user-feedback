"""Quoting helpers and format() (PostgreSQL)."""
import re

from .basic import FunctionError

# Identifier characters PostgreSQL accepts without quoting.
_SIMPLE_IDENT = re.compile(r"^[a-z_][a-z0-9_$]*$")

# Reserved keywords that quote_ident still wraps in double quotes.
_RESERVED = frozenset("""
all analyse analyze and any array as asc asymmetric authorization binary both
case cast check collate collation column concurrently constraint create cross
current_catalog current_date current_role current_schema current_time
current_timestamp current_user default deferrable desc distinct do else end
except false fetch filter for foreign freeze from full grant group having ilike
in initially inner intersect into is isnull join lateral leading left like
limit locally natural not notnull null offset on only or order outer overlaps
placing primary references returning right select session_user similar some
symmetric table tablesample then to trailing true union unique user using
variadic verbose when where window with
""".split())


def quote_ident(s):
    if s is None:
        return None
    if _SIMPLE_IDENT.match(s) and s not in _RESERVED:
        return s
    return '"' + s.replace('"', '""') + '"'


def _text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def quote_literal(s):
    # STRICT in PostgreSQL: NULL -> NULL (use quote_nullable for NULL text).
    if s is None:
        return None
    return "'" + _text(s).replace("'", "''") + "'"


def quote_nullable(s):
    if s is None:
        return "NULL"
    return quote_literal(s)


_SPEC_START = re.compile(r"%(?:(\d+)\$)?(-)?(\d+)?([sIL%])")


def format_(fmt, *args):
    if fmt is None:
        return None
    out = []
    pos = 0
    auto_index = 0
    positional = None  # False = plain %s style, True = n$ style
    size = len(fmt)
    while pos < size:
        pct = fmt.find("%", pos)
        if pct < 0:
            out.append(fmt[pos:])
            break
        out.append(fmt[pos:pct])
        m = _SPEC_START.match(fmt, pct)
        if not m:
            raise FunctionError("unsupported format specification")
        kind = m.group(4)
        if kind == "%":
            if m.group(1) or m.group(2) or m.group(3):
                raise FunctionError("unsupported format specification")
            out.append("%")
            pos = m.end()
            continue
        if m.group(1):
            if positional is False:
                raise FunctionError("mixing positional and named placeholders")
            positional = True
            index = int(m.group(1)) - 1
            if index < 0 or index >= len(args):
                raise FunctionError("argument index out of range")
        else:
            if positional is True:
                raise FunctionError("mixing positional and named placeholders")
            positional = False
            index = auto_index
            auto_index += 1
            if index >= len(args):
                raise FunctionError("too few arguments for format()")
        value = args[index]
        if kind == "s":
            # NULL formats as an empty string, never the text "None".
            text = "" if value is None else _text(value)
        elif kind == "I":
            text = quote_ident("" if value is None else _text(value))
        else:  # "L"
            text = quote_nullable(value)
        width = int(m.group(3)) if m.group(3) else 0
        text = text.ljust(width) if m.group(2) else text.rjust(width)
        out.append(text)
        pos = m.end()
    return "".join(out)
