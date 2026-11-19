"""Type input and casts of the import/ETL layer (PostgreSQL rules).

  cast(value, from_type, to_type)   from_type is "text" for input from text
Types: int2, int4, int8, numeric, numeric(p,s), float8, bool, text, varchar(n), char(n).
"""
from decimal import Decimal, ROUND_HALF_UP
import re


class CastError(Exception):
    pass


RANGES = {"int2": 2**15, "int4": 2**31, "int8": 2**63}


def _int_from_text(s, typ):
    try:
        v = int(s)
    except ValueError:
        v = int(float(s))
    return v


def cast(value, src, dst):
    if value is None:
        return None
    m = re.fullmatch(r"(numeric|varchar|char)\((\d+)(?:,(\d+))?\)", dst)
    if m:
        kind, n, s = m.group(1), int(m.group(2)), m.group(3)
        if kind == "numeric":
            return Decimal(str(value)).quantize(Decimal(1).scaleb(-int(s or 0)), rounding=ROUND_HALF_UP)
        if kind == "varchar":
            return str(value)[:n]
        return str(value)[:n]
    if dst in RANGES:
        if src == "text":
            return _int_from_text(value, dst)
        if src in ("numeric", "float8"):
            return int(round(Decimal(str(value))))
        if src == "bool":
            return int(value)
        return int(value)
    if dst == "numeric":
        return Decimal(str(value).strip())
    if dst == "float8":
        return float(value)
    if dst == "bool":
        if src == "text":
            return str(value).strip().lower() in ("t", "true", "yes", "y", "on", "1")
        return bool(value)
    if dst == "text":
        return str(value)
    raise CastError(f"cannot cast type {src} to {dst}")


def text_out(value, typ):
    """How psql shows a value of the given type."""
    if value is None:
        return None
    if typ == "bool":
        return "t" if value else "f"
    if typ == "float8":
        return repr(float(value))
    return str(value)
