#!/usr/bin/env python3
"""Replay a scripted interleaving of locking transactions."""
import argparse
import json

from engine import run_script

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["run", "show"])
parser.add_argument("script")
args = parser.parse_args()
out = run_script(json.load(open(args.script, encoding="utf-8")))
if args.command == "run":
    print(json.dumps(out, indent=1))
else:
    for r in out["results"]:
        extra = "".join(f" {k}={r[k]}" for k in ("rows", "error", "resumed_at") if k in r)
        print(f"{r['step']:>2} tx{r['tx']} {r['op']:<8} {r['status']}{' WAITED' if r['waited'] else ''}{extra}")
    print("table:", out["table"])
