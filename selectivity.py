"""Selectivity estimation from column statistics and access path choice."""
import operator
from bisect import bisect_right

SEQ_COST_PER_ROW, INDEX_START, INDEX_COST_PER_ROW = 1.0, 2.0, 4.0
OPS = {"=": operator.eq, "!=": operator.ne, "<": operator.lt,
       "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def rest_frac(st):
    """Share of rows that are non-NULL and outside the MCV list."""
    return max(0.0, 1.0 - st["null_frac"] - sum(f for _, f in st["mcv"]))


def eq(st, value):
    if value is None:
        return 0.0
    for v, freq in st["mcv"]:
        if v == value:
            return freq
    left = st["ndv"] - len(st["mcv"])
    if left <= 0:
        return 0.0
    return rest_frac(st) / left


def below(hist, c, inclusive):
    """Fraction of histogram rows below c (at or below when inclusive)."""
    if not hist:
        return 0.0
    if len(hist) == 1 or c < hist[0] or c > hist[-1] or hist[0] == hist[-1]:
        return 1.0 if (c > hist[-1] or (inclusive and c >= hist[-1])) else 0.0
    i = min(bisect_right(hist, c) - 1, len(hist) - 2)
    lo, hi = hist[i], hist[i + 1]
    inside = (c - lo) / (hi - lo) if hi > lo else 1.0
    return (i + inside) / (len(hist) - 1)


def range_sel(st, op, c):
    mcv_part = sum(f for v, f in st["mcv"] if OPS[op](v, c))
    inclusive = op in ("<=", ">=")
    part = below(st["histogram"], c, inclusive)
    if op in (">", ">="):
        part = 1.0 - below(st["histogram"], c, not inclusive)
    return mcv_part + rest_frac(st) * part


def estimate(stats, expr):
    if "and" in expr:
        out = 1.0
        for e in expr["and"]:
            out *= estimate(stats, e)
        return out
    if "or" in expr:
        out = 0.0
        for e in expr["or"]:
            s = estimate(stats, e)
            out = out + s - out * s
        return out
    st, op = stats["columns"][expr["col"]], expr["op"]
    if op == "is_null":
        return st["null_frac"]
    if op == "not_null":
        return 1.0 - st["null_frac"]
    if op == "=":
        return eq(st, expr["value"])
    if op == "!=":
        return max(0.0, 1.0 - st["null_frac"] - eq(st, expr["value"]))
    if op == "in":
        distinct = dict.fromkeys(expr["values"])
        return min(1.0 - st["null_frac"], sum(eq(st, v) for v in distinct))
    return range_sel(st, op, expr["value"])


def columns_of(expr):
    if "and" in expr or "or" in expr:
        return {c for e in expr.get("and", expr.get("or")) for c in columns_of(e)}
    return {expr["col"]}


def access_path(stats, expr, indexes, sel):
    rows = sel * stats["rows"]
    seq = stats["rows"] * SEQ_COST_PER_ROW
    if columns_of(expr) <= set(indexes) and INDEX_START + rows * INDEX_COST_PER_ROW < seq:
        return "index"
    return "seq"
