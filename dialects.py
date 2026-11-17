"""Mock executors for the legacy and the target dialect over in-memory tables."""
import functools
import json
import zlib
from datetime import datetime, timedelta

from sqlparse import Parser


def offset(zone):
    sign = -1 if zone[0] == "-" else 1
    h, m = zone[1:].split(":")
    return timedelta(hours=sign * int(h), minutes=sign * int(m))


def utc_to_local(value, zone):
    """'2026-03-01T16:30:00Z' -> '2026-03-02 00:30:00' in `zone`."""
    t = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ") + offset(zone)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def local_to_utc(value, zone):
    t = datetime.strptime(value, "%Y-%m-%d %H:%M:%S") - offset(zone)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def evaluate(e, row):
    if e[0] == "lit":
        return e[1]
    if e[0] == "col":
        return row.get(e[1])
    name, args = e[1], [evaluate(a, row) for a in e[2]]
    if any(a is None for a in args):
        return None
    if name in ("LOWER", "UPPER"):
        return args[0].lower() if name == "LOWER" else args[0].upper()
    if name == "DATE":
        return args[0][:10]
    if name == "CONVERT_TZ":
        t = datetime.strptime(args[0], "%Y-%m-%d %H:%M:%S") - offset(args[1]) + offset(args[2])
        return t.strftime("%Y-%m-%d %H:%M:%S")
    if name == "DATE_TRUNC":
        # Rewritten form: DATE_TRUNC('day', ts[, zone]); hand-written: DATE_TRUNC(ts[, zone]).
        # A bare timestamp never matches a truncation unit, so the first string is
        # the unit only when it is one of the known units.
        units = {"day", "hour", "minute", "second"}
        if isinstance(args[0], str) and args[0] in units:
            ts = args[1]
            zone = args[2] if len(args) > 2 else "+00:00"
        else:
            ts = args[0]
            zone = args[1] if len(args) > 1 else "+00:00"
        return utc_to_local(ts, zone)[:10]
    if name == "TIMEZONE":
        return utc_to_local(args[1], args[0])
    raise ValueError(f"unknown function {name}")


def compare(a, b, fold):
    if fold and isinstance(a, str):
        a, b = a.lower(), b.lower()
    return (a > b) - (a < b)


def execute(dialect, query, rows, primary_key=None):
    """Run a query (SQL text or parsed) over `rows` with the rules of 'legacy' or 'target'."""
    q = Parser(query).parse() if isinstance(query, str) else query
    legacy = dialect == "legacy"
    eq = (lambda a, b: a is not None and compare(a, b, legacy) == 0)
    hits = [r for r in rows if all(eq(evaluate(a, r), evaluate(b, r)) for a, b in q["where"])]
    # A primary key in the sort keys makes the order total, so ties are fully
    # resolved and the top-N order must not change with LIMIT/OFFSET. Without it,
    # the target engine, like a real top-N sort, returns ties in an unstable order.
    totally_ordered = any(e[0] == "col" and e[1] == primary_key for e, _, _ in q["order"])
    if not legacy and q["limit"] is not None and not totally_ordered:
        salt = str(q["limit"] + q["offset"])
        hits.sort(key=lambda r: zlib.crc32((salt + json.dumps(r, sort_keys=True)).encode()))

    def order(r1, r2):
        for e, desc, nulls in q["order"]:
            a, b = evaluate(e, r1), evaluate(e, r2)
            if a is None or b is None:
                if a is None and b is None:
                    continue
                first = nulls == "FIRST" if nulls else (legacy != desc)
                return -1 if (a is None) == first else 1
            c = compare(a, b, legacy)
            if c:
                return -c if desc else c
        return 0

    hits.sort(key=functools.cmp_to_key(order))
    end = None if q["limit"] is None else q["offset"] + q["limit"]
    return [{alias: evaluate(e, r) for e, alias in q["items"]} for r in hits[q["offset"]:end]]
