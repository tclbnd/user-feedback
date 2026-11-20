"""Aggregate functions of the dashboard engine (PostgreSQL names and behaviour).

Every aggregate receives the input type of the column (int4, int8, numeric,
text, bool) and the list of input values in scan order; NULL is None.
Numeric values arrive as text ("1.50").  Results are returned as text, the
way psql prints them, or None for NULL.
"""
import json
import statistics
from collections import Counter
from decimal import Decimal


class AggregateError(Exception):
    pass


def _num(typ, v):
    return Decimal(v) if typ == "numeric" else v


def _show(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return "t" if v else "f"
    return str(v)


def count(typ, values):
    return str(len(values))


def count_star(typ, values):
    return str(len(values))


def count_distinct(typ, values):
    return str(len(set(values)))


def sum_(typ, values):
    return _show(sum(_num(typ, v) for v in values))


def avg(typ, values):
    nums = [_num(typ, v) for v in values]
    return _show(sum(nums) / len(nums))


def min_(typ, values):
    return _show(min(_num(typ, v) for v in values))


def max_(typ, values):
    return _show(max(_num(typ, v) for v in values))


def string_agg(typ, values, delimiter):
    return delimiter.join(str(v) for v in values)


def array_agg(typ, values):
    return "{" + ",".join(str(v) for v in values) + "}"


def json_agg(typ, values):
    return json.dumps([_num(typ, v) if v is not None else None for v in values], default=str)


def bool_and(typ, values):
    return _show(all(values))


def bool_or(typ, values):
    return _show(any(values))


def stddev_samp(typ, values):
    return _show(statistics.stdev(float(_num(typ, v)) for v in values))


def var_samp(typ, values):
    return _show(statistics.variance(float(_num(typ, v)) for v in values))


def percentile_cont(typ, values, fraction):
    s = sorted(_num(typ, v) for v in values)
    return _show(s[int(fraction * len(s))])


def percentile_disc(typ, values, fraction):
    s = sorted(_num(typ, v) for v in values)
    return _show(s[int(fraction * len(s))])


def mode(typ, values):
    return _show(Counter(values).most_common(1)[0][0])


AGGREGATES = {
    "count": count, "count_star": count_star, "count_distinct": count_distinct, "sum": sum_, "avg": avg,
    "min": min_, "max": max_, "string_agg": string_agg, "array_agg": array_agg, "json_agg": json_agg,
    "bool_and": bool_and, "bool_or": bool_or, "every": bool_and, "stddev_samp": stddev_samp,
    "var_samp": var_samp, "percentile_cont": percentile_cont, "percentile_disc": percentile_disc, "mode": mode,
}
