#!/usr/bin/env python3
"""Deterministic session replay + diff attribution.

Replay rules (see README.md):
  * events execute strictly in seq order;
  * fetch never performs a real request -- the recorded snapshot is used;
  * parse applies the code version active at the event to the fetch payload;
  * ts/random are never generated -- recorded values are passed through;
  * unknown event kinds are reported in errors and skipped.

Mismatch attribution is evaluated per field with precedence:
  input-drift > code-change > order-sensitive > real-defect.
Each diff entry carries locating evidence (lineage, active version,
switch point, snapshot vs recorded response).

The engine is chunk-safe: replay_events carries explicit state, so
replaying chunks [0:k], [k:n] gives results identical to a full replay.
"""
import json
import sys

_MISSING = object()


# ---------------------------------------------------------------- versions
def normalize_title(value, version):
    """Version-specific title rule.

    v1: title kept as-is.
    v2: trim surrounding whitespace and lower-case.
    v3: v2 rule, plus an empty (post-trim) title is emitted as null.
    """
    if value is None:
        return None if version == "v3" else value
    if version == "v1":
        return value
    if not isinstance(value, str):
        return value
    trimmed = value.strip().lower()
    if version == "v3" and trimmed == "":
        return None
    return trimmed


# ------------------------------------------------------------- event replay
def initial_state():
    """Fresh replay state; also the hand-off unit for chunked replay."""
    return {
        "version": None,
        "outputs": [],          # emitted records, seq order
        "errors": [],           # structured replay errors
        "fetch_payload": {},    # fetch seq -> payload used at replay time
        "fetch_drift": {},      # fetch seq -> recorded-vs-snapshot evidence
        "parsed": {},           # parse seq -> processed fields
        "lineage": {},          # parse seq -> field lineage info
        "emit_lineage": {},     # emit seq -> field lineage info
        "seen_ids": {},         # record id -> first emit seq
    }


def replay_events(events, snapshots, base_version, state=None):
    """Replay an already seq-ordered event list against recorded snapshots."""
    st = state if state is not None else initial_state()
    fetch_snap = (snapshots or {}).get("fetch", {})

    for e in events:
        seq = e["seq"]
        kind = e.get("kind")

        # event-level version override governs this and every later event
        if "code_version" in e:
            st["version"] = e["code_version"]
        elif st["version"] is None:
            st["version"] = base_version
        active = st["version"]

        if kind == "fetch":
            url = e.get("url")
            recorded_resp = e.get("response", _MISSING)
            if url in fetch_snap:
                payload = fetch_snap[url]
            elif recorded_resp is not _MISSING:
                payload = recorded_resp
            else:
                st["errors"].append({
                    "seq": seq, "reason": "missing-snapshot",
                    "detail": "no snapshot for url %r and no recorded "
                              "response" % (url,)})
                st["fetch_payload"][seq] = None
                continue
            st["fetch_payload"][seq] = payload
            if recorded_resp is not _MISSING and recorded_resp != payload:
                # external input drifted between event log and snapshot
                st["fetch_drift"][seq] = {"recorded": recorded_resp,
                                          "snapshot": payload}

        elif kind == "parse":
            ref = e.get("input_ref")
            if ref not in st["fetch_payload"]:
                st["errors"].append({
                    "seq": seq, "reason": "missing-ref",
                    "detail": "input_ref %s does not resolve to a fetch "
                              "event" % (ref,)})
                st["parsed"][seq] = {}
                st["lineage"][seq] = {}
                continue
            payload = st["fetch_payload"][ref]
            if isinstance(payload, dict):
                fields = dict(payload)
            else:
                fields = {"content": payload}
            lineage = {}
            if "title" in fields:
                fields["title"] = normalize_title(fields["title"], active)
            for fld in fields:
                lineage[fld] = {"src": "parse:%s" % seq,
                                "fetch_seq": ref, "version": active}
            st["parsed"][seq] = fields
            st["lineage"][seq] = lineage

        elif kind == "emit":
            rec = dict(e.get("record") or {})
            rec_lineage = {f: {"src": "emit", "fetch_seq": None,
                               "version": active} for f in rec}
            ref = e.get("fields_ref")
            if ref is not None:
                if ref not in st["parsed"]:
                    st["errors"].append({
                        "seq": seq, "reason": "missing-ref",
                        "detail": "fields_ref %s does not resolve to a "
                                  "parse event" % (ref,)})
                else:
                    for fld, val in st["parsed"][ref].items():
                        rec[fld] = val
                        rec_lineage[fld] = st["lineage"].get(ref, {}).get(
                            fld, {"src": "parse:%s" % seq,
                                  "fetch_seq": None, "version": active})
            rid = rec.get("id")
            if rid is not None and rid in st["seen_ids"]:
                st["errors"].append({
                    "seq": seq, "reason": "duplicate-id",
                    "detail": "id %r already emitted at seq %s"
                              % (rid, st["seen_ids"][rid])})
            elif rid is not None:
                st["seen_ids"][rid] = seq
            st["outputs"].append(rec)
            st["emit_lineage"][seq] = rec_lineage

        elif kind in ("ts", "random"):
            # temporal / random values: deterministic pass-through only
            pass
        else:
            st["errors"].append({"seq": seq, "reason": "unknown-kind",
                                 "detail": repr(kind)})
    return st


# ------------------------------------------------------------ log integrity
def check_truncation(events):
    """A complete log covers seq 0..n with no gaps. Error dict or None."""
    seqs = sorted(e["seq"] for e in events)
    if not seqs:
        return {"seq": -1, "reason": "truncated-log",
                "detail": "event log is empty", "missing": [0]}
    present = set(seqs)
    missing = [s for s in range(0, seqs[-1] + 1) if s not in present]
    if seqs[0] != 0 or missing:
        gap = missing if seqs[0] == 0 else [0] + missing
        return {"seq": -1, "reason": "truncated-log",
                "detail": "event log is not a contiguous 0..n sequence",
                "missing": _compress(gap)}
    return None


def _compress(nums):
    """[0,1,2,4] -> [[0,2],[4,4]]: compact missing-segment ranges."""
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append([start, prev])
            start = prev = n
    if start is not None:
        out.append([start, prev])
    return out


# --------------------------------------------------------- order diagnostics
def order_inversions(events):
    """Pairs (a, b) whose position order disagrees with seq order."""
    inv = set()
    pos = {e["seq"]: i for i, e in enumerate(events)}
    seqs = list(pos)
    for i in range(len(seqs)):
        for j in range(i + 1, len(seqs)):
            a, b = seqs[i], seqs[j]
            if a > b and pos[a] < pos[b]:
                inv.add((b, a))
    return sorted(inv)


def _range_for(seq, ranges):
    for r in ranges:
        if r["from_seq"] <= seq <= r["to_seq"]:
            return r
    return None


def _field_cause(fld, info, ctx):
    """Attribute one mismatching field, with locating evidence."""
    fetch_seq = info.get("fetch_seq")
    src = info.get("src", "")
    ref_seq = int(src.split(":")[1]) if src.startswith("parse:") else \
        ctx["lineage_seq"]
    # 1) external input changed between event record and snapshot
    if fetch_seq is not None and fetch_seq in ctx["drift"]:
        d = ctx["drift"][fetch_seq]
        return "input-drift", {
            "field": fld, "fetch_seq": fetch_seq,
            "recorded_response": d["recorded"],
            "snapshot_value": d["snapshot"]}
    # 2) field was produced after a code-version switch point
    ver = info.get("version")
    rng = _range_for(ref_seq, ctx["ranges"])
    if rng is not None and rng.get("switched"):
        return "code-change", {
            "field": fld, "active_version": ver,
            "switch_at_seq": rng["from_seq"],
            "base_version": ctx["base_version"],
            "evidence": "field processed under %s after version switch at "
                        "seq %s" % (ver, rng["from_seq"])}
    # 3) upstream event participates in an out-of-order pair
    for a, b in ctx["inversions"]:
        if ref_seq is not None and (ref_seq == a or ref_seq == b):
            return "order-sensitive", {
                "field": fld, "inversion": [a, b],
                "evidence": "upstream parse seq %s participates in ordering "
                            "pair [%s, %s]" % (ref_seq, a, b)}
    # 4) identical snapshot and version, deterministic replay still differs
    return "real-defect", {
        "field": fld, "active_version": ver,
        "evidence": "deterministic replay under identical snapshot and "
                    "version still disagrees"}


# --------------------------------------------------------------- comparison
def compare(state, events, raw_events, base_version, ranges):
    recorded = state.get("recorded_outputs", [])
    rec_map = {}
    for r in recorded:
        rec_map.setdefault(r.get("id"), r)

    replay_map = {r["id"]: r for r in state["outputs"] if "id" in r}
    emit_seq = {}
    for e in events:
        if e.get("kind") == "emit" and isinstance(e.get("record"), dict) \
                and "id" in e["record"]:
            emit_seq.setdefault(e["record"]["id"], e["seq"])

    inversions = order_inversions(raw_events)
    diffs = []

    # mismatches and extra emissions, walking replay output in seq order
    for rec in state["outputs"]:
        rid = rec.get("id")
        seq = emit_seq.get(rid, -1)
        if rid not in rec_map:
            diffs.append({"seq": seq, "kind": "emit-extra",
                          "cause": "real-defect",
                          "detail": {"id": rid,
                                     "evidence": "emitted record absent from "
                                                 "recorded_outputs"}})
            continue
        expected = rec_map[rid]
        field_causes, field_evidence = [], {}
        for fld in sorted(set(rec) | set(expected)):
            rv = rec.get(fld, _MISSING)
            tv = expected.get(fld, _MISSING)
            if rv != tv:
                info = state["emit_lineage"].get(seq, {}).get(
                    fld, {"src": "emit", "fetch_seq": None,
                          "version": state["version"]})
                cause, evidence = _field_cause(
                    fld, info,
                    {"drift": state["fetch_drift"],
                     "ranges": ranges,
                     "inversions": inversions,
                     "base_version": base_version,
                     "lineage_seq": seq})
                field_causes.append(cause)
                field_evidence[fld] = dict(
                    evidence,
                    replayed=None if rv is _MISSING else rv,
                    recorded=None if tv is _MISSING else tv)
        if field_causes:
            priority = {"input-drift": 4, "code-change": 3,
                        "order-sensitive": 2, "real-defect": 1}
            primary = max(field_causes, key=lambda c: priority[c])
            diffs.append({"seq": seq, "kind": "emit-mismatch",
                          "cause": primary,
                          "detail": {"id": rid,
                                     "field_causes": field_causes,
                                     "fields": field_evidence}})

    # recorded records that replay never produced
    for r in recorded:
        rid = r.get("id")
        if rid not in replay_map:
            diffs.append({"seq": emit_seq.get(rid, -1),
                          "kind": "emit-missing", "cause": "real-defect",
                          "detail": {"id": rid,
                                     "evidence": "recorded output has no "
                                                 "matching emit event"}})

    diffs.sort(key=lambda d: (d["seq"], d["kind"]))
    return diffs


# ------------------------------------------------------------- version map
def compute_version_ranges(events, base_version):
    """Contiguous seq ranges with the code version actually active.

    A range carries switched=True when its version was entered through an
    explicit event-level code_version override (the opening range at seq 0
    merely inherits the session default and is not a switch).
    """
    ranges, cur_ver, start, switched = [], None, 0, False
    for e in events:
        if "code_version" in e:
            ver, hit = e["code_version"], True
        elif cur_ver is not None:
            ver, hit = cur_ver, False
        else:
            ver, hit = base_version, False
        if cur_ver is None:
            cur_ver, start, switched = ver, e["seq"], hit
        elif ver != cur_ver:
            ranges.append({"from_seq": start, "to_seq": e["seq"] - 1,
                           "version": cur_ver, "switched": switched})
            cur_ver, start, switched = ver, e["seq"], hit
    if cur_ver is not None:
        ranges.append({"from_seq": start, "to_seq": events[-1]["seq"],
                       "version": cur_ver, "switched": switched})
    return ranges


# -------------------------------------------------------------------- entry
def run(session):
    raw_events = session["events"]
    events = sorted(raw_events, key=lambda e: e["seq"])
    base_version = session.get("code_version", "v1")

    trunc = check_truncation(raw_events)
    state = replay_events(events, session.get("snapshots", {}), base_version)
    state["recorded_outputs"] = session.get("recorded_outputs", [])
    if trunc is not None:
        state["errors"].append(trunc)

    ranges = compute_version_ranges(events, base_version)
    # ordering diagnostics must see the original (unsorted) log order
    diffs = compare(state, events, raw_events, base_version, ranges)
    return {"replayed_outputs": state["outputs"], "diff": diffs,
            "errors": state["errors"], "version_ranges": ranges}


def run_chunked(session, chunk_size):
    """Replay the same session in fixed-size seq-ordered chunks."""
    raw_events = session["events"]
    events = sorted(raw_events, key=lambda e: e["seq"])
    base_version = session.get("code_version", "v1")
    state = initial_state()
    for i in range(0, len(events), chunk_size):
        replay_events(events[i:i + chunk_size],
                      session.get("snapshots", {}), base_version, state)
    state["recorded_outputs"] = session.get("recorded_outputs", [])
    trunc = check_truncation(raw_events)
    if trunc is not None:
        state["errors"].append(trunc)
    ranges = compute_version_ranges(events, base_version)
    diffs = compare(state, events, raw_events, base_version, ranges)
    return {"replayed_outputs": state["outputs"], "diff": diffs,
            "errors": state["errors"], "version_ranges": ranges}


def main():
    if len(sys.argv) < 3:
        print("usage: replay_audit.py session.json output.json [chunk_size]")
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as fh:
        session = json.load(fh)
    if len(sys.argv) >= 4:
        result = run_chunked(session, int(sys.argv[3]))
    else:
        result = run(session)
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


if __name__ == "__main__":
    main()
