#!/usr/bin/env python3
"""Normalize a legacy wide table from its functional dependencies."""
import argparse
import csv
import json
import os
import sys

from design import analyze, sql, violations
from fd import closure, minimal_cover

ap = argparse.ArgumentParser()
ap.add_argument("command", choices=["analyze", "closure", "migrate"])
ap.add_argument("spec")
ap.add_argument("args", nargs="*", help="closure: A,B   migrate: <sample.csv> <outdir>")
a = ap.parse_args()
spec = json.load(open(a.spec, encoding="utf-8"))
fds = [(f["lhs"], f["rhs"]) for f in spec["fds"]]
if a.command == "closure":
    plus = closure(a.args[0].split(","), fds)
    print(json.dumps([x for x in spec["attributes"] if x in plus]))
    sys.exit(0)
report = analyze(spec, minimal_cover(fds, spec["attributes"]))
if a.command == "analyze":
    print(json.dumps(report, indent=1))
    sys.exit(0)
rows = list(csv.DictReader(open(a.args[0], encoding="utf-8", newline="")))
bad = violations(spec, rows)
if bad:
    print(json.dumps({"violations": bad}, indent=1))
    sys.exit(3)
os.makedirs(a.args[1], exist_ok=True)
schema, migrate = sql(spec, report)
open(os.path.join(a.args[1], "schema.sql"), "w", encoding="utf-8").write(schema)
open(os.path.join(a.args[1], "migrate.sql"), "w", encoding="utf-8").write(migrate)
print(schema + migrate, end="")
