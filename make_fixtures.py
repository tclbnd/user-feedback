#!/usr/bin/env python3
"""Generate deterministic replay test sessions.

Sessions S1..S5 each carry exactly one injected discrepancy class:
  S1 code-change      version switch v1 -> v2 changes title processing
  S2 input-drift      snapshot fetch value differs from event response
  S3 order-sensitive  log order disagrees with seq dependency order
  S4 real-defect      bad amount field / extra emit / missing emit
  S5 code-change v3   v1 -> v3 switch, empty title becomes null
Plus:
  S0 clean            no differences (empty diff set expected)
  S6 truncated-log    seq gap 2..3 missing -> truncated-log error
  S7 unknown-kind     unknown event kind -> unknown-kind error, clean diffs
All randomness/time values are recorded; no network or clock is used.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")


def save(name, session):
    with open(os.path.join(FIX, name), "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


# ------------------------------------------------------------ S1 code-change
def s1_code_change():
    # recorded outputs were produced under v1 semantics (title kept verbatim);
    # replay switches to v2 at seq 3, so titles after the switch are trimmed
    # and lower-cased -> mismatch attributable to the version interval.
    events = [
        {"seq": 0, "kind": "fetch", "url": "u1",
         "response": {"title": "  Hello World  "}, "ts": 100},
        {"seq": 1, "kind": "parse", "input_ref": 0, "ts": 101},
        {"seq": 2, "kind": "emit", "fields_ref": 1,
         "record": {"id": "r1"}, "ts": 102},
        {"seq": 3, "kind": "fetch", "url": "u2", "code_version": "v2",
         "response": {"title": " Bank Statement "}, "ts": 103},
        {"seq": 4, "kind": "parse", "input_ref": 3, "ts": 104},
        {"seq": 5, "kind": "emit", "fields_ref": 4,
         "record": {"id": "r2"}, "ts": 105},
    ]
    snapshots = {"fetch": {"u1": {"title": "  Hello World  "},
                           "u2": {"title": " Bank Statement "}}}
    recorded = [{"id": "r1", "title": "  Hello World  "},
                {"id": "r2", "title": " Bank Statement "}]
    return {"session": "S1-code-change", "code_version": "v1",
            "events": events, "snapshots": snapshots,
            "recorded_outputs": recorded}


# ------------------------------------------------------------- S2 input drift
def s2_input_drift():
    # the event log recorded response X, but the authoritative snapshot
    # holds X' -> replay output diverges through no code/order reason.
    events = [
        {"seq": 0, "kind": "fetch", "url": "rate",
         "response": {"title": "rate-A", "value": 100}, "ts": 200},
        {"seq": 1, "kind": "parse", "input_ref": 0, "ts": 201},
        {"seq": 2, "kind": "emit", "fields_ref": 1,
         "record": {"id": "d1"}, "ts": 202},
    ]
    snapshots = {"fetch": {"rate": {"title": "rate-B", "value": 200}}}
    recorded = [{"id": "d1", "title": "rate-A", "value": 100}]
    return {"session": "S2-input-drift", "code_version": "v1",
            "events": events, "snapshots": snapshots,
            "recorded_outputs": recorded}


# -------------------------------------------------------- S3 order-sensitive
def s3_order_sensitive():
    # dependency chain: parse 4 needs fetch 3; the log lists parse 4 before
    # fetch 3, so a naive in-log-order replay fails. Seq-ordered replay
    # succeeds; the recorded output captured the broken ordering.
    events = [
        {"seq": 0, "kind": "fetch", "url": "a",
         "response": {"title": "First"}, "ts": 1},
        {"seq": 4, "kind": "parse", "input_ref": 3, "ts": 5},
        {"seq": 5, "kind": "emit", "fields_ref": 4,
         "record": {"id": "o2"}, "ts": 6},
        {"seq": 1, "kind": "parse", "input_ref": 0, "ts": 2},
        {"seq": 2, "kind": "emit", "fields_ref": 1,
         "record": {"id": "o1"}, "ts": 3},
        {"seq": 3, "kind": "fetch", "url": "b",
         "response": {"title": "Second"}, "ts": 4},
    ]
    snapshots = {"fetch": {"a": {"title": "First"},
                           "b": {"title": "Second"}}}
    # o1 replays clean; o2 was recorded with the broken ordering
    recorded = [{"id": "o1", "title": "First"},
                {"id": "o2", "title": None}]
    return {"session": "S3-order-sensitive", "code_version": "v1",
            "events": events, "snapshots": snapshots,
            "recorded_outputs": recorded}


# ------------------------------------------------------------- S4 real defect
def s4_real_defect():
    # identical version, identical snapshot, contiguous log; the recorded
    # amount is simply wrong, plus an extra and a missing record.
    events = [
        {"seq": 0, "kind": "fetch", "url": "acct",
         "response": {"title": "acct", "amount": 50}, "ts": 10},
        {"seq": 1, "kind": "parse", "input_ref": 0, "ts": 11},
        {"seq": 2, "kind": "emit", "fields_ref": 1,
         "record": {"id": "x1"}, "ts": 12},
        {"seq": 3, "kind": "emit",
         "record": {"id": "x2", "title": "ghost", "amount": 9}, "ts": 13},
    ]
    snapshots = {"fetch": {"acct": {"title": "acct", "amount": 50}}}
    recorded = [
        {"id": "x1", "title": "acct", "amount": 999},   # wrong amount
        {"id": "x3", "title": "missing"},               # never emitted
    ]
    return {"session": "S4-real-defect", "code_version": "v1",
            "events": events, "snapshots": snapshots,
            "recorded_outputs": recorded}


# ------------------------------------------------------ S5 v3 null semantics
def s5_code_change_v3():
    # v1 keeps the blank title; v3 normalises a blank title to null.
    events = [
        {"seq": 0, "kind": "fetch", "url": "p1",
         "response": {"title": "Ledger"}, "ts": 0},
        {"seq": 1, "kind": "parse", "input_ref": 0, "ts": 1},
        {"seq": 2, "kind": "emit", "fields_ref": 1,
         "record": {"id": "n1"}, "ts": 2},
        {"seq": 3, "kind": "fetch", "url": "p2", "code_version": "v3",
         "response": {"title": "   "}, "ts": 3},
        {"seq": 4, "kind": "parse", "input_ref": 3, "ts": 4},
        {"seq": 5, "kind": "emit", "fields_ref": 4,
         "record": {"id": "n2"}, "ts": 5},
    ]
    snapshots = {"fetch": {"p1": {"title": "Ledger"},
                           "p2": {"title": "   "}}}
    recorded = [{"id": "n1", "title": "Ledger"},
                {"id": "n2", "title": "   "}]
    return {"session": "S5-code-change-v3", "code_version": "v1",
            "events": events, "snapshots": snapshots,
            "recorded_outputs": recorded}


# ----------------------------------------------------------------- S0 clean
def s0_clean():
    # recorded outputs exactly match deterministic replay; includes a
    # string payload (exposed as content) and recorded ts/random pass-through.
    events = [
        {"seq": 0, "kind": "fetch", "url": "c1",
         "response": {"title": "Stable", "k": 1}, "ts": 7},
        {"seq": 1, "kind": "parse", "input_ref": 0, "ts": 8,
         "random": 0.42},
        {"seq": 2, "kind": "emit", "fields_ref": 1,
         "record": {"id": "c-r1"}, "ts": 9},
        {"seq": 3, "kind": "fetch", "url": "c2",
         "response": "raw-text", "ts": 10},
        {"seq": 4, "kind": "parse", "input_ref": 3, "ts": 11},
        {"seq": 5, "kind": "emit", "fields_ref": 4,
         "record": {"id": "c-r2"}, "ts": 12},
    ]
    snapshots = {"fetch": {"c1": {"title": "Stable", "k": 1},
                           "c2": "raw-text"}}
    recorded = [{"id": "c-r1", "title": "Stable", "k": 1},
                {"id": "c-r2", "content": "raw-text"}]
    return {"session": "S0-clean", "code_version": "v1",
            "events": events, "snapshots": snapshots,
            "recorded_outputs": recorded}


# ------------------------------------------------------------- S6 truncated
def s6_truncated():
    # seqs 0,1,4,5 present -> gap 2..3; the later emit then dangles.
    events = [
        {"seq": 0, "kind": "fetch", "url": "t1",
         "response": {"title": "before-cut"}, "ts": 0},
        {"seq": 1, "kind": "parse", "input_ref": 0, "ts": 1},
        {"seq": 4, "kind": "parse", "input_ref": 3, "ts": 4},
        {"seq": 5, "kind": "emit", "fields_ref": 4,
         "record": {"id": "t2"}, "ts": 5},
    ]
    snapshots = {"fetch": {"t1": {"title": "before-cut"}}}
    # the dangling emit resolves to no parsed fields, matching this record,
    # so the session's only findings are the truncation / missing-ref errors
    recorded = [{"id": "t2"}]
    return {"session": "S6-truncated", "code_version": "v1",
            "events": events, "snapshots": snapshots,
            "recorded_outputs": recorded}


# ---------------------------------------------------------- S7 unknown kind
def s7_unknown_kind():
    events = [
        {"seq": 0, "kind": "fetch", "url": "k1",
         "response": {"title": "ok"}, "ts": 0},
        {"seq": 1, "kind": "telemetry", "payload": "unsupported", "ts": 1},
        {"seq": 2, "kind": "parse", "input_ref": 0, "ts": 2},
        {"seq": 3, "kind": "emit", "fields_ref": 2,
         "record": {"id": "k-r1"}, "ts": 3},
    ]
    snapshots = {"fetch": {"k1": {"title": "ok"}}}
    recorded = [{"id": "k-r1", "title": "ok"}]
    return {"session": "S7-unknown-kind", "code_version": "v1",
            "events": events, "snapshots": snapshots,
            "recorded_outputs": recorded}


def main():
    os.makedirs(FIX, exist_ok=True)
    sessions = {
        "S0_clean.json": s0_clean(),
        "S1_code_change.json": s1_code_change(),
        "S2_input_drift.json": s2_input_drift(),
        "S3_order_sensitive.json": s3_order_sensitive(),
        "S4_real_defect.json": s4_real_defect(),
        "S5_code_change_v3.json": s5_code_change_v3(),
        "S6_truncated.json": s6_truncated(),
        "S7_unknown_kind.json": s7_unknown_kind(),
    }
    for name, sess in sessions.items():
        save(name, sess)
    print("wrote %d sessions to %s" % (len(sessions), FIX))


if __name__ == "__main__":
    main()
