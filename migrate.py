#!/usr/bin/env python3
"""Rewrite legacy queries for the target dialect and validate them on both executors."""
import argparse
import json

import dialects
from sqlparse import Parser, render


def rewrite(sql, data):
    q = Parser(sql).parse()
    zone = data["legacy_zone"]
    texts, stamps = set(data["text_columns"][q["table"]]), set(data["timestamp_columns"][q["table"]])

    def expr(e):
        if e[0] == "fn" and e[1] == "DATE":
            return ("fn", "DATE_TRUNC", [("lit", "day"), expr(e[2][0])])
        if e[0] == "fn" and e[1] == "CONVERT_TZ":
            if e[2][1] != ("lit", zone):
                raise SystemExit(f"CONVERT_TZ must convert from the session zone {zone}")
            return ("fn", "TIMEZONE", [e[2][2], expr(e[2][0])])
        if e[0] == "fn":
            return ("fn", e[1], [expr(a) for a in e[2]])
        return e

    def folded(e):
        return ("fn", "LOWER", [e]) if e[0] == "col" and e[1] in texts else expr(e)

    q["items"] = [(expr(e) if not (e[0] == "col" and e[1] in stamps) else ("fn", "TIMEZONE", [("lit", zone), e]), a)
                  for e, a in q["items"]]
    q["where"] = [(folded(a), ("lit", b[1].lower()) if b[0] == "lit" and isinstance(b[1], str) else b)
                  for a, b in q["where"]]
    q["order"] = [(expr(e), d, n or "FIRST") for e, d, n in q["order"]]
    return render(q)


def stabilized(sql, data):
    q = Parser(sql).parse()
    pk = data["primary_key"][q["table"]]
    if ("col", pk) not in [e for e, _, _ in q["order"]]:
        q["order"].append((("col", pk), False, None))
    return q


def check(data, queries):
    report = []
    for item in queries:
        table = Parser(item["source"]).parse()["table"]
        target_sql = item.get("target") or rewrite(item["source"], data)
        want = dialects.execute("legacy", stabilized(item["source"], data), data["legacy"][table])
        got = dialects.execute("target", target_sql, data["target"][table])
        entry = {"name": item["name"], "target_sql": target_sql, "status": "match", "cause": None,
                 "legacy_rows": len(want), "target_rows": len(got)}
        if got != want:
            entry.update(status="mismatch", cause="data" if len(got) != len(want) else "rewrite",
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
        for row in dialects.execute(args.dialect, args.arg, data[args.dialect][table]):
            print(json.dumps(row, ensure_ascii=False))
    else:
        for e in check(data, json.load(open(args.arg, encoding="utf-8"))):
            print(json.dumps(e, ensure_ascii=False))


if __name__ == "__main__":
    main()
