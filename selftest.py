#!/usr/bin/env python3
"""Self-tests for replay.py.

Covers:
  1. 3-generation event stream: replay.py output matches an independent
     reference implementation field by field.
  2. Missing upcast path -> explicit reject located by seq.
  3. Two-way split folded in parallel threads + merge == single-stream fold.
  4. Snapshot resume (incl. --fail-after crash) == full fold, field by field.
  5. CLI compatibility: python3 replay.py events.json output.json.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from replay import Replayer

# ---------------------------------------------------------------------------
# Independent reference implementation, written straight from the README.
# ---------------------------------------------------------------------------
def reference(events):
    state = {"balance_cents": 0, "name": "", "applied": 0}
    rejects, byv = [], {}
    for ev in events:
        t, v, d = ev.get("type"), ev.get("v"), ev.get("data") or {}
        ok = True
        if t == "rename":
            state["name"] = d["name"]
        elif t in ("credit", "debit"):
            if v == 1:
                cents = int(d["amount"])
            elif v == 2:
                if d.get("currency") != "CNY":
                    rejects.append({"seq": ev["seq"], "reason":
                                    "missing-currency" if d.get("currency") is None
                                    else "unsupported-currency"})
                    ok = False
                    cents = 0
                else:
                    cents = int(d["cents"])
            elif v == 3:
                cents = int(Decimal(d["amount"]) * 100)
            else:
                rejects.append({"seq": ev["seq"],
                                "reason": "no-upcast-path:v%s->v3" % v})
                ok, cents = False, 0
            if ok:
                state["balance_cents"] += cents if t == "credit" else -cents
        else:
            rejects.append({"seq": ev["seq"], "reason": "unknown-type"})
            ok = False
        if ok:
            state["applied"] += 1
            byv[str(v)] = byv.get(str(v), 0) + 1
    return {"state": state, "rejects": rejects, "applied_by_version": byv}


def sample_events():
    return [
        {"seq": 0, "type": "credit", "v": 1, "data": {"amount": 1000}},      # +1000
        {"seq": 1, "type": "credit", "v": 2, "data": {"cents": 250, "currency": "CNY"}},  # +250
        {"seq": 2, "type": "debit",  "v": 3, "data": {"amount": "1.50"}},    # -150
        {"seq": 3, "type": "rename", "v": 4, "data": {"name": "alice"}},
        {"seq": 4, "type": "credit", "v": 2, "data": {"cents": 9, "currency": "USD"}},   # reject
        {"seq": 5, "type": "credit", "v": 3, "data": {"amount": "2.25", "currency": "EUR"}},  # +225 (v3 tolerates any/missing currency)
        {"seq": 6, "type": "debit",  "v": 1, "data": {"amount": 100}},       # -100
        {"seq": 7, "type": "bogus",  "v": 1, "data": {}},                    # reject unknown-type
        {"seq": 8, "type": "credit", "v": 9, "data": {"amount": 5}},         # reject no-upcast-path
        {"seq": 9, "type": "rename", "v": 2, "data": {"name": "bob"}},
        {"seq": 10, "type": "debit", "v": 3, "data": {"amount": "0.05", "currency": "CNY"}},  # -5
    ]


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" -- " + detail if detail and not cond else "")))
    if not cond:
        sys.exit(1)


def main():
    events = sample_events()

    # 1. 3-generation stream: replay.py vs reference, field by field.
    r = Replayer()
    r.fold(events)
    got = r.report()
    ref = reference(events)
    check("state matches reference", got["state"] == ref["state"],
          json.dumps((got["state"], ref["state"])))
    check("rejects match reference", got["rejects"] == ref["rejects"],
          json.dumps((got["rejects"], ref["rejects"])))
    check("applied_by_version matches reference",
          got["applied_by_version"] == ref["applied_by_version"],
          json.dumps((got["applied_by_version"], ref["applied_by_version"])))

    # 2. Missing upcast path is an explicit, seq-located error.
    miss = [x for x in got["rejects"] if x["reason"].startswith("no-upcast-path")]
    check("missing path located by seq", miss == [{"seq": 8, "reason": "no-upcast-path:v9->v3"}],
          json.dumps(miss))
    check("migration record per applied event",
          [m["seq"] for m in got["migrations"]] == [0, 1, 2, 3, 5, 6, 9, 10])
    check("v1 event migrated through full chain",
          got["migrations"][0]["path"] == [1, 2, 3])

    # 3. Same prefix -> same state (determinism).
    r2 = Replayer()
    r2.fold(events[:5])
    r3 = Replayer()
    r3.fold(events[:5])
    check("same prefix -> same state", r2.report() == r3.report())

    # 4. Two-way split, folded in parallel threads, merged == single fold.
    ev_a = [e for e in events if e["seq"] % 2 == 0]
    ev_b = [e for e in events if e["seq"] % 2 == 1]
    ra, rb = Replayer(), Replayer()
    ta = threading.Thread(target=ra.fold, args=(ev_a,))
    tb = threading.Thread(target=rb.fold, args=(ev_b,))
    ta.start(); tb.start(); ta.join(); tb.join()
    merged = ra.merge(rb).report()
    check("parallel split + merge == single fold (state)",
          merged["state"] == got["state"],
          json.dumps((merged["state"], got["state"])))
    check("parallel split + merge == single fold (rejects)",
          merged["rejects"] == got["rejects"])
    check("parallel split + merge == single fold (by_version)",
          merged["applied_by_version"] == got["applied_by_version"])

    # 5. CLI + snapshot: full run vs crash (--fail-after) + resume.
    tmp = tempfile.mkdtemp(prefix="replay-test-")
    inp = os.path.join(tmp, "events.json")
    out_full = os.path.join(tmp, "out_full.json")
    out_resumed = os.path.join(tmp, "out_resumed.json")
    snap = os.path.join(tmp, "snap.json")
    here = os.path.dirname(os.path.abspath(__file__))
    with open(inp, "w") as f:
        json.dump({"events": events}, f)

    p = subprocess.run([sys.executable, os.path.join(here, "replay.py"), inp, out_full])
    check("CLI full run exits 0", p.returncode == 0)

    p = subprocess.run([sys.executable, os.path.join(here, "replay.py"), inp,
                        out_resumed, "--snapshot", snap, "--fail-after", "4"])
    check("crash run exits 3", p.returncode == 3, "rc=%d" % p.returncode)
    check("crash run wrote checkpoint snapshot", os.path.exists(snap))
    snap_mid = json.load(open(snap))
    check("snapshot caches migrated state",
          snap_mid["last_seq"] == 3 and snap_mid["state"]["balance_cents"] == 1100,
          json.dumps(snap_mid))

    p = subprocess.run([sys.executable, os.path.join(here, "replay.py"), inp,
                        out_resumed, "--snapshot", snap])
    check("resume run exits 0", p.returncode == 0)
    full = json.load(open(out_full))
    resumed = json.load(open(out_resumed))
    check("snapshot resume == full fold (all fields)", resumed == full,
          json.dumps((resumed, full)))

    # 6. Snapshot idempotence: third run with no new events changes nothing.
    out_again = os.path.join(tmp, "out_again.json")
    subprocess.run([sys.executable, os.path.join(here, "replay.py"), inp,
                    out_again, "--snapshot", snap], check=True)
    check("re-run on completed snapshot is a no-op",
          json.load(open(out_again)) == full)

    print("\nAll tests passed.")
    print("Full-fold output:", json.dumps(full, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
