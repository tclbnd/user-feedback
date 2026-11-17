"""Selectivity estimation from column statistics and access path choice."""
from bisect import bisect_right

SEQ_COST_PER_ROW, INDEX_START, INDEX_COST_PER_ROW = 1.0, 2.0, 4.0


def eq(st, value):
    return (1.0 - st["null_frac"]) / st["ndv"] if st["ndv"] else 0.0


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
    inclusive = op in ("<=", ">=")
    part = below(st["histogram"], c, inclusive)
    if op in (">", ">="):
        part = 1.0 - below(st["histogram"], c, not inclusive)
    return (1.0 - st["null_frac"]) * part


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
            out = min(1.0, out + s)
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
        return min(1.0 - st["null_frac"], sum(eq(st, v) for v in expr["values"]))
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
