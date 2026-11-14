#!/usr/bin/env python3
"""Fold an event stream into aggregate state, with snapshot resume."""
import json, sys

def apply(state, ev, rejects):
    v, t, d = ev.get("v"), ev.get("type"), ev.get("data") or {}
    if t not in ("credit", "debit", "rename"):
        rejects.append({"seq": ev["seq"], "reason": "unknown-type"})
        return
    if t == "rename":
        state["name"] = d["name"]; state["applied"] += 1; return
    amt = d.get("amount")            # BUG: reads 'amount' for all versions
    cents = int(amt * 100) if isinstance(amt, float) else int(amt)
    state["balance_cents"] += cents if t == "credit" else -cents
    state["applied"] += 1

def fold(events, state, rejects, byv, fail_after=None):
    for i, ev in enumerate(events):
        if fail_after is not None and i >= fail_after:
            sys.exit(3)
        v = str(ev.get("v"))
        before = len(rejects)
        apply(state, ev, rejects)
        if len(rejects) == before:
            byv[v] = byv.get(v, 0) + 1

def main():
    req_in, out_p = sys.argv[1], sys.argv[2]
    snap_p = None
    fail_after = None
    a = sys.argv[3:]
    for i, x in enumerate(a):
        if x == "--snapshot": snap_p = a[i + 1]
        if x == "--fail-after": fail_after = int(a[i + 1])
    events = json.load(open(req_in))["events"]
    state = {"balance_cents": 0, "name": "", "applied": 0}
    last_seq = -1
    if snap_p:
        try:
            snap = json.load(open(snap_p))
            state = snap["state"]; last_seq = snap["last_seq"]
        except FileNotFoundError:
            pass
    rejects, byv = [], {}
    todo = [e for e in events if e["seq"] > last_seq]
    fold(todo, state, rejects, byv, fail_after)
    json.dump({"state": state, "rejects": rejects, "applied_by_version": byv},
              open(out_p, "w"))
    if snap_p:
        json.dump({"last_seq": events[-1]["seq"] if events else last_seq, "state": state},
                  open(snap_p, "w"))

if __name__ == "__main__":
    main()
