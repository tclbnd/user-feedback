"""Evaluate numeric function calls.

    python3 numcall.py calls.json result.json

calls.json: [["round", ["numeric", "2.5"]], ["round", ["float8", 2.5]], ["round", ["numeric", "1234.567"], 2],
             ["trunc", ["numeric", "-1.99"]], ["div", ["numeric", "-7"], ["numeric", "2"]],
             ["mod", ["int", -7], ["int", 2]], ["/", ["int", 7], ["int", 2]], ["log", ["numeric", "100"]],
             ["width_bucket", ["numeric", "5.35"], ["numeric", "0.024"], ["numeric", "10.06"], 5], ...]
Results are rendered as text the way psql shows them.
"""
import json
import sys
from decimal import Decimal

from numfn import funcs
from numfn.funcs import NumericError

NAMES = {"round": funcs.round_, "trunc": funcs.trunc, "ceil": funcs.ceil_, "floor": funcs.floor_, "div": funcs.div,
         "mod": funcs.mod, "/": funcs.int_divide, "log": funcs.log, "sqrt": funcs.sqrt_, "power": funcs.power,
         "sign": funcs.sign, "width_bucket": funcs.width_bucket, "gcd": funcs.gcd, "lcm": funcs.lcm}


def show(result):
    t, v = result
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def call(c):
    try:
        args = [tuple(a) if isinstance(a, list) else a for a in c[1:]]
        return {"call": c, "value": show(NAMES[c[0]](*args))}
    except (NumericError, ArithmeticError, ValueError) as exc:
        return {"call": c, "error": f"{type(exc).__name__}: {exc}"}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
