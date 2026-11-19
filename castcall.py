"""Evaluate casts and show results as psql would.

    python3 castcall.py calls.json result.json

calls.json: [["' 12 '", "text", "int4"], ["3.5", "numeric", "int4"], ["2.5", "float8", "int4"],
             ["yes", "text", "bool"], ["0.30000000000000004", "float8", "text"], ["123.456", "numeric", "numeric(5,2)"],
             ["abcdef", "text", "varchar(3)"], ["ab", "text", "char(5)"], ...]
Each item: [value, from_type, to_type]; values are given as text or JSON literals.
Result: {"value": <psql text>} or {"error": ...}; for char(n) results also "length" (length() of the value).
"""
import json
import sys
from decimal import Decimal

from casts.convert import CastError, cast, normalize_type, text_out


def source_value(v, typ):
    if v is None:
        return None
    if typ == "numeric":
        return v if isinstance(v, Decimal) else Decimal(str(v))
    if typ == "float8":
        return float(v)
    if typ in ("int2", "int4", "int8"):
        return int(v)
    if typ == "bool":
        if isinstance(v, bool):
            return v
        return cast(str(v), "text", "bool")
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        return text_out(v, "numeric")
    return str(v)


def call(c):
    v, src, dst = c
    src = normalize_type(src)
    dst = normalize_type(dst)
    try:
        out = cast(source_value(v, src), src, dst)
        res = {"call": c, "value": text_out(out, dst.split("(")[0])}
        if dst.startswith("char(") or dst == "char":
            # length() on bpchar does not count trailing spaces
            res["length"] = len(out.rstrip(" "))
        return res
    except (CastError, ValueError, ArithmeticError) as exc:
        return {"call": c, "error": f"{type(exc).__name__}: {exc}"}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"), parse_float=Decimal)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
