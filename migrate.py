#!/usr/bin/env python3
"""Rewrite legacy queries for the target dialect and validate them on both executors."""
import argparse
import json

import dialects
from sqlparse import Parser, render


def rewrite(sql, data):
    q = Parser(sql).parse()
    table = q["table"]
    zone = data["legacy_zone"]
    texts = set(data["text_columns"].get(table, []))
    stamps = set(data["timestamp_columns"].get(table, []))

    def expr(e):
        """Rewrite functions: DATE -> DATE_TRUNC(day, ..., zone), CONVERT_TZ -> TIMEZONE."""
        if e[0] == "fn" and e[1] == "DATE":
            return ("fn", "DATE_TRUNC", [("lit", "day"), expr(e[2][0]), ("lit", zone)])
        if e[0] == "fn" and e[1] == "CONVERT_TZ":
            if e[2][1] != ("lit", zone):
                raise SystemExit(f"CONVERT_TZ must convert from the session zone {zone}")
            return ("fn", "TIMEZONE", [e[2][2], expr(e[2][0])])
        if e[0] == "fn":
            return ("fn", e[1], [expr(a) for a in e[2]])
        return e

    def folded(e):
        """Case-insensitive text semantics on the target: wrap text columns in LOWER
        and lowercase string literals compared against them."""
        e = expr(e)
        if e[0] == "col" and e[1] in texts:
            return ("fn", "LOWER", [e])
        if e[0] == "lit" and isinstance(e[1], str):
            return ("lit", e[1].lower())
        if e[0] == "fn":
            return ("fn", e[1], [folded(a) for a in e[2]])
        return e

    # A bare timestamp column is selected through TIMEZONE(zone, ts) so both sides
    # print the same naive local text.
    q["items"] = [(expr(e) if not (e[0] == "col" and e[1] in stamps)
                   else ("fn", "TIMEZONE", [("lit", zone), e]), a)
                  for e, a in q["items"]]
    q["where"] = [(folded(a), folded(b)) for a, b in q["where"]]
    # Legacy treats NULL as the lowest value: FIRST under ASC, LAST under DESC.
    q["order"] = [(folded(e), d, n or ("FIRST" if not d else "LAST"))
                  for e, d, n in q["order"]]

    # Append the primary key as a final ascending tiebreaker so the order is total
    # and paging over equal sort keys never repeats or skips rows.
    pk = data["primary_key"][table]
    if ("col", pk) not in [e for e, _, _ in q["order"]]:
        q["order"].append((("col", pk), False, "FIRST"))
    return render(q)


def stabilized(sql, data):
    q = Parser(sql).parse()
    pk = data["primary_key"][q["table"]]
    if ("col", pk) not in [e for e, _, _ in q["order"]]:
        q["order"].append((("col", pk), False, None))
    return q


def mirror_legacy(data, table):
    """A target-side copy of the legacy table: naive session-zone timestamps are
    converted to UTC, everything else is copied unchanged."""
    zone = data["legacy_zone"]
    stamps = set(data["timestamp_columns"].get(table, []))
    mirrored = []
    for row in data["legacy"][table]:
        r = dict(row)
        for col in stamps:
            if r.get(col) is not None:
                r[col] = dialects.local_to_utc(r[col], zone)
        mirrored.append(r)
    return mirrored


def check(data, queries):
    report = []
    for item in queries:
        table = Parser(item["source"]).parse()["table"]
        target_sql = item.get("target") or rewrite(item["source"], data)
        # The legacy side gets the primary key appended as a final tiebreaker so
        # both result sets are totally ordered and comparable row by row.
        want = dialects.execute("legacy", stabilized(item["source"], data), data["legacy"][table])
        got = dialects.execute("target", target_sql, data["target"][table],
                               primary_key=data["primary_key"][table])
        entry = {"name": item["name"], "target_sql": target_sql, "status": "match", "cause": None,
                 "legacy_rows": len(want), "target_rows": len(got)}
        if got != want:
            # Re-run the target SQL on a UTC-mirrored copy of the legacy data. If it
            # reproduces the legacy result, the rewrite is sound and the migrated
            # target data itself differs; otherwise the rewrite is at fault.
            mirrored = dialects.execute("target", target_sql, mirror_legacy(data, table),
                                        primary_key=data["primary_key"][table])
            cause = "data" if mirrored == want else "rewrite"
            entry.update(status="mismatch", cause=cause,
                         first_diff=next((i for i, (a, b) in enumerate(zip(want, got)) if a != b),
                                         min(len(want), len(got))))
        report.append(entry)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["rewrite", "run", "check"])
    ap.add_argument("dataset")
    ap.add_argument("arg", help="SQL text, or a queries.json file for check")
    ap.add_argument("--dialect", choices=["legacy", "target"], default="legacy")
    args = ap.parse_args()
    data = json.load(open(args.dataset, encoding="utf-8"))
    if args.command == "rewrite":
        print(rewrite(args.arg, data))
    elif args.command == "run":
        table = Parser(args.arg).parse()["table"]
        pk = data["primary_key"].get(table)
        for row in dialects.execute(args.dialect, args.arg, data[args.dialect][table],
                                    primary_key=pk):
            print(json.dumps(row, ensure_ascii=False))
    else:
        for e in check(data, json.load(open(args.arg, encoding="utf-8"))):
            print(json.dumps(e, ensure_ascii=False))


if __name__ == "__main__":
    main()
