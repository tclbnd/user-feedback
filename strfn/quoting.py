"""Quoting helpers and format() (PostgreSQL)."""
import re

from .basic import FunctionError


def quote_ident(s):
    if s is None:
        return None
    return '"' + s.replace('"', '""') + '"'


def quote_literal(s):
    if s is None:
        return None
    return "'" + str(s).replace("'", "''") + "'"


def quote_nullable(s):
    return quote_literal(s)


SPEC = re.compile(r"%(\d+\$)?(-)?(\d+)?([sIL%])")


def format_(fmt, *args):
    if fmt is None:
        return None
    out, pos, argi = [], 0, 0
    for m in SPEC.finditer(fmt):
        out.append(fmt[pos:m.start()])
        pos = m.end()
        kind = m.group(4)
        if kind == "%":
            out.append("%")
            continue
        if m.group(1):
            argi = int(m.group(1)[:-1]) - 1
        if argi >= len(args):
            raise FunctionError("too few arguments for format()")
        value = args[argi]
        argi += 1
        if kind == "s":
            text = str(value)
        elif kind == "I":
            text = quote_ident(str(value))
        else:
            text = quote_literal(value)
        width = int(m.group(3)) if m.group(3) else 0
        text = text.ljust(width) if m.group(2) else text.rjust(width)
        out.append(text)
    out.append(fmt[pos:])
    return "".join(out)
