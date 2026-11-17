#!/usr/bin/env python3
"""Replay an interleaved transaction schedule: ssidb.py run|report <schedule.json>."""
import json
import sys

from engine import Engine, SerializationFailure

FAILURE = {"error": "serialization_failure", "code": "40001", "retryable": True}


def step(engine, op):
    kind = op["op"]
    if kind == "begin":
        engine.begin(op["tx"], op.get("read_only", False))
        return {"ok": True}
    tx = engine.txns[op["tx"]]
    if tx.status != "running":
        return {"error": tx.status}
    try:
        if kind == "get":
            return {"value": engine.read(tx, op["key"])}
        if kind == "scan":
            return {"rows": engine.read(tx, op["lo"], op["hi"])}
        if kind in ("put", "delete"):
            if tx.read_only:
                return {"error": "read_only"}
            engine.write(tx, op["key"], op.get("value"))
        elif kind == "commit":
            engine.commit(tx)
        elif kind == "abort":
            engine.abort(tx)
        else:
            raise SystemExit(f"unknown op {kind}")
        return {"ok": True}
    except SerializationFailure:
        return dict(FAILURE)


def report(ops, out):
    for n, (op, res) in enumerate(zip(ops, out["results"]), 1):
        args = "read_only" if op.get("read_only") else " ".join(
            f"{k}={op[k]}" for k in ("key", "lo", "hi", "value") if k in op)
        shown = " ".join(str(res[k]) for k in ("error", "code") if k in res) or (
            "rows " + json.dumps(res["rows"]) if "rows" in res else
            "value " + json.dumps(res["value"]) if "value" in res else "ok")
        print(f"{n:3d} {op['tx']:>3} {op['op']:<7}{args:<18} {shown}")
    print("committed:", " ".join(out["committed"]) or "-")
    print("aborted:  ", " ".join(out["aborted"]) or "-")
    print("state:    ", json.dumps(out["state"], ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("run", "report"):
        raise SystemExit("usage: ssidb.py run|report <schedule.json>")
    with open(sys.argv[2], encoding="utf-8") as fh:
        schedule = json.load(fh)
    engine = Engine(schedule.get("initial"))
    out = {"results": [step(engine, op) for op in schedule["ops"]], **engine.outcome()}
    if sys.argv[1] == "report":
        report(schedule["ops"], out)
    else:
        print(json.dumps(out, ensure_ascii=False))
