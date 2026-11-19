"""Evaluate date/time function calls.

    python3 dtcall.py calls.json result.json

calls.json examples:
  ["extract", "dow", "2026-11-22"]                 date argument as ISO text
  ["date_trunc", "week", "2026-11-22 10:00:00"]
  ["ts_plus_interval", "2026-01-31 00:00:00", [1, 0, 0]]   interval as [months, days, seconds]
  ["date_plus_int", "2026-02-28", 1]    ["date_minus_date", "2026-03-01", "2026-02-01"]
  ["make_date", 2026, 2, 30]            ["justify_days", [0, 35, 0]]
  ["interval_out", [0, -1, 7200]]       ["to_char", "2026-11-20 00:30:00", "FMDay HH12:MI AM"]
"""
import json
import sys

from dtfn import funcs
from dtfn.tochar import to_char
from dtfn.values import DateTimeError, parse_date, parse_ts, show_date, show_interval, show_ts


def call(c):
    name, a = c[0], c[1:]
    try:
        if name == "extract":
            v = funcs.extract(a[0], parse_date(a[1][:10]))
        elif name == "date_trunc":
            v = show_ts(funcs.date_trunc(a[0], parse_ts(a[1])))
        elif name == "ts_plus_interval":
            v = show_ts(funcs.add_interval(parse_ts(a[0]), *a[1]))
        elif name == "date_plus_int":
            v = show_date(funcs.date_plus_int(parse_date(a[0]), a[1]))
        elif name == "date_minus_date":
            v = funcs.date_minus_date(parse_date(a[0]), parse_date(a[1]))
        elif name == "make_date":
            v = show_date(funcs.make_date(*a))
        elif name in ("justify_days", "justify_hours", "justify_interval"):
            v = show_interval(*getattr(funcs, name)(*a[0]))
        elif name == "interval_out":
            v = show_interval(*a[0])
        elif name == "to_char":
            v = to_char(parse_ts(a[0]), a[1])
        else:
            raise DateTimeError(f"function {name} does not exist")
        return {"call": c, "value": v}
    except DateTimeError as exc:
        return {"call": c, "error": str(exc)}


def main():
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([call(c) for c in calls], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
