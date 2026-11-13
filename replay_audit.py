#!/usr/bin/env python3
"""Deterministic session replay + diff attribution.

CLI (backward compatible):
    python3 replay_audit.py session.json output.json [--chunks N]

Replay rules (see README.md):
  * events are executed strictly in ``seq`` order;
  * ``fetch`` never performs a real request, it reads the frozen value from
    ``snapshots.fetch[url]`` (the event's recorded ``response`` is kept only
    for drift comparison);
  * ``parse`` applies the code version in effect at that event to the
    response fields reached through ``input_ref``;
  * ``emit`` produces one record into ``replayed_outputs``;
  * ``ts`` / randomness are never regenerated -- recorded values are used;
  * unknown event kinds go to ``errors`` and replay continues.

The replay is a pure left fold over the event stream, so a stream can be
replayed in contiguous chunks while threading the same state object and
yield a result identical to a whole-stream replay (see ``--chunks``).
"""
import argparse
import json

# Diff causes / kinds (contract vocabulary from README.md).
CAUSE_CODE = "code-change"
CAUSE_INPUT = "input-drift"
CAUSE_ORDER = "order-sensitive"
CAUSE_DEFECT = "real-defect"

KIND_MISMATCH = "emit-mismatch"
KIND_EXTRA = "emit-extra"
KIND_MISSING = "emit-missing"


# --------------------------------------------------------------------------
# Versioned processing logic
# --------------------------------------------------------------------------
def transform_title(value, version):
    """Apply the title rule of ``version`` to a recorded field value."""
    if not isinstance(value, str):
        return value
    if version == "v1":
        return value
    text = value.strip().lower()
    if version == "v3" and text == "":
        return None
    return text


def new_state(default_version):
    """Create the mutable fold state for one replay."""
    return {
        "version": default_version,
        "default_version": default_version,
        # seq -> {"url", "recorded", "snapshot", "drift", "version"}
        "fetches": {},
        # seq -> {"fields", "input_ref", "version"}
        "parsed": {},
        # emit output records paired with their source seq
        "outputs": [],
        "emit_seqs": [],
        "errors": [],
        # {"seq", "from", "to"} in encounter (seq) order
        "switches": [],
    }


def _snapshot_fields(snapshot_response, event_fields):
    """Resolve the fields a parse event must process.

    External inputs are authoritative per snapshot: when the frozen response
    carries structured fields they win; otherwise the parse event's recorded
    ``fields`` are used.
    """
    if isinstance(snapshot_response, dict):
        nested = snapshot_response.get("fields")
        if isinstance(nested, dict):
            return dict(nested)
        if "title" in snapshot_response:
            return dict(snapshot_response)
    return dict(event_fields or {})


def process_event(state, event, snapshots):
    """Fold a single event into the replay state. Pure / deterministic."""
    seq = event["seq"]
    kind = event.get("kind")

    # Event-level code version override takes effect at this event and
    # remains in effect for every later event.
    marker = event.get("code_version")
    if marker is not None and marker != state["version"]:
        state["switches"].append(
            {"seq": seq, "from": state["version"], "to": marker})
        state["version"] = marker

    if kind == "fetch":
        url = event.get("url")
        recorded_response = event.get("response")
        fetch_snaps = (snapshots or {}).get("fetch", {}) \
            if isinstance(snapshots, dict) else {}
        if isinstance(fetch_snaps, dict) and url in fetch_snaps:
            snapshot_response = fetch_snaps[url]
            from_snapshot = True
        else:
            snapshot_response = recorded_response
            from_snapshot = False
        state["fetches"][seq] = {
            "seq": seq,
            "url": url,
            "recorded": recorded_response,
            "snapshot": snapshot_response,
            "drift": from_snapshot
                     and snapshot_response != recorded_response,
            "version": state["version"],
        }

    elif kind == "parse":
        ref = event.get("input_ref")
        fetch_info = state["fetches"].get(ref)
        snapshot_response = fetch_info["snapshot"] if fetch_info else None
        base_fields = _snapshot_fields(
            snapshot_response, event.get("fields") or {})
        fields = {}
        for key, value in base_fields.items():
            if key == "title":
                fields[key] = transform_title(value, state["version"])
            else:
                fields[key] = value
        state["parsed"][seq] = {
            "fields": fields,
            "input_ref": ref,
            "version": state["version"],
        }

    elif kind == "emit":
        record = dict(event.get("record") or {})
        ref = event.get("fields_ref")
        if ref is not None and ref in state["parsed"]:
            record.update(state["parsed"][ref]["fields"])
        state["outputs"].append(record)
        state["emit_seqs"].append(seq)

    else:
        state["errors"].append({"seq": seq, "reason": "unknown-kind"})

    return state


def fold_events(state, events, snapshots):
    """Fold an ordered iterable of events into ``state``."""
    for event in events:
        process_event(state, event, snapshots)
    return state


# --------------------------------------------------------------------------
# Report construction
# --------------------------------------------------------------------------
def _chunk(items, n):
    """Split a list into ``n`` contiguous, near-equal chunks."""
    n = max(1, min(n, len(items) or 1))
    step = max(1, (len(items) + n - 1) // n)
    return [items[i:i + step] for i in range(0, len(items), step)]


def build_version_ranges(ordered_events, default_version, max_seq):
    """Partition the seq space into ranges with one active code version."""
    ranges = []
    current = default_version
    start = 0
    for event in ordered_events:
        marker = event.get("code_version")
        if marker is not None and marker != current:
            if start <= event["seq"] - 1:
                ranges.append({"from_seq": start,
                               "to_seq": event["seq"] - 1,
                               "version": current})
            current = marker
            start = event["seq"]
    if start <= max_seq:
        ranges.append({"from_seq": start,
                       "to_seq": max_seq,
                       "version": current})
    return ranges


def _range_at(ranges, seq):
    for rng in ranges:
        if rng["from_seq"] <= seq <= rng["to_seq"]:
            return rng
    return None


def _emit_chain(state, emit_event):
    """Return (parse_seq, parse_info, fetch_info) feeding an emit."""
    parse_seq = emit_event.get("fields_ref")
    parse_info = state["parsed"].get(parse_seq) \
        if parse_seq is not None else None
    fetch_info = None
    if parse_info is not None:
        fetch_info = state["fetches"].get(parse_info.get("input_ref"))
    return parse_seq, parse_info, fetch_info


def _field_cause(state, ranges, emit_event, emit_seq, field):
    """Attribute one mismatching field, with locating evidence."""
    parse_seq, parse_info, fetch_info = _emit_chain(state, emit_event)
    anchor_seq = parse_seq if parse_seq is not None else emit_seq
    switched = [sw for sw in state["switches"] if sw["seq"] <= anchor_seq]

    if field == "title" and parse_info is not None and switched:
        sw = switched[-1]
        rng = _range_at(ranges, anchor_seq)
        rng_text = ("version range %s-%s=%s"
                    % (rng["from_seq"], rng["to_seq"], rng["version"])
                    if rng else "version %s" % sw["to"])
        return CAUSE_CODE, (
            "field '%s' processed after version switch at seq %s (%s->%s); %s"
            % (field, sw["seq"], sw["from"], sw["to"], rng_text))

    if fetch_info is not None and fetch_info.get("drift"):
        return CAUSE_INPUT, (
            "field '%s' fed by drifted fetch at seq %s url=%r: "
            "event response=%r snapshot=%r"
            % (field, fetch_info["seq"], fetch_info["url"],
               fetch_info["recorded"], fetch_info["snapshot"]))

    return CAUSE_DEFECT, "no version switch or input drift explains field '%s'" % field


_CAUSE_RANK = {CAUSE_CODE: 0, CAUSE_INPUT: 1, CAUSE_ORDER: 2, CAUSE_DEFECT: 3}


def _diff_records(state, ranges, ordered_events):
    """Align replayed / recorded records by id and attribute differences."""
    diffs = []
    outputs = state["outputs"]
    emit_seqs = state["emit_seqs"]
    # ordered_events maps seq -> event for emit lookup.
    emit_by_seq = {e["seq"]: e for e in ordered_events
                   if e.get("kind") == "emit"}
    recorded = state.get("_recorded", [])

    recorded_by_id = {}
    for rec in recorded:
        recorded_by_id.setdefault(rec.get("id"), rec)
    consumed_ids = set()

    # Physical log order vs canonical seq order (order-sensitivity signal).
    physical_anomaly = any(i != ev["seq"]
                           for i, ev in enumerate(state.get("_events_raw", [])))

    for record, emit_seq in zip(outputs, emit_seqs):
        rid = record.get("id")
        if rid not in recorded_by_id:
            diffs.append({
                "seq": emit_seq,
                "kind": KIND_EXTRA,
                "cause": CAUSE_DEFECT,
                "detail": "replayed emit id=%r absent from recorded_outputs"
                          % rid,
            })
            continue
        consumed_ids.add(rid)
        expected = recorded_by_id[rid]
        if record == expected:
            continue
        fields = sorted(set(record) | set(expected))
        per_field = []
        for field in fields:
            if record.get(field) == expected.get(field):
                continue
            cause, evidence = _field_cause(
                state, ranges, emit_by_seq[emit_seq], emit_seq, field)
            per_field.append((field, cause, evidence,
                              record.get(field), expected.get(field)))
        if not per_field:
            continue
        top = min(per_field, key=lambda item: _CAUSE_RANK[item[1]])[1]
        detail = "; ".join(
            "field '%s' replayed=%r recorded=%r [%s: %s]"
            % (field, replayed, recorded_value, cause, evidence)
            for field, cause, evidence, replayed, recorded_value in per_field)
        diffs.append({
            "seq": emit_seq,
            "kind": KIND_MISMATCH,
            "cause": top,
            "detail": detail,
        })

    for rec in recorded:
        if rec.get("id") not in consumed_ids:
            diffs.append({
                "seq": -1,
                "kind": KIND_MISSING,
                "cause": CAUSE_DEFECT,
                "detail": "recorded id=%r has no matching replayed emit"
                          % rec.get("id"),
            })

    # Pure reordering: identical id sets and identical payloads, different
    # emission order between collection and deterministic replay.
    if not diffs and len(outputs) == len(recorded) and outputs:
        replayed_ids = [rec.get("id") for rec in outputs]
        recorded_ids = [rec.get("id") for rec in recorded]
        if replayed_ids != recorded_ids \
                and sorted(replayed_ids, key=str) \
                == sorted(recorded_ids, key=str):
            recorded_pos = {rid: i for i, rid in enumerate(recorded_ids)}
            for record, emit_seq in zip(outputs, emit_seqs):
                rid = record.get("id")
                replayed_pos = replayed_ids.index(rid)
                if replayed_pos != recorded_pos[rid]:
                    evidence = ("recorded position %s vs replayed position %s"
                                % (recorded_pos[rid], replayed_pos))
                    if physical_anomaly:
                        evidence += "; event log physical order differs from seq order"
                    diffs.append({
                        "seq": emit_seq,
                        "kind": KIND_MISMATCH,
                        "cause": CAUSE_ORDER,
                        "detail": "emit id=%r order-sensitive (%s)"
                                  % (rid, evidence),
                    })
    return diffs


def run(session, chunks=1):
    """Replay one session and build the comparison report."""
    raw_events = session.get("events", [])
    snapshots = session.get("snapshots", {})
    default_version = session.get("code_version")

    ordered_events = sorted(raw_events, key=lambda ev: ev["seq"])
    state = new_state(default_version)
    state["_events_raw"] = raw_events
    state["_recorded"] = session.get("recorded_outputs", [])

    if chunks and chunks > 1 and ordered_events:
        for chunk in _chunk(ordered_events, chunks):
            fold_events(state, chunk, snapshots)
    else:
        fold_events(state, ordered_events, snapshots)

    seqs = [ev["seq"] for ev in ordered_events]
    max_seq = max(seqs) if seqs else -1
    errors = list(state["errors"])

    # Truncation: seq values must be the contiguous set 0..max_seq.
    present = set(seqs)
    missing = [s for s in range(max_seq + 1) if s not in present]
    if missing:
        errors.append({"seq": -1, "reason": "truncated-log",
                       "missing": missing})
    elif len(seqs) != len(present):
        duplicates = sorted({s for s in seqs if seqs.count(s) > 1})
        errors.append({"seq": -1, "reason": "duplicate-seq",
                       "duplicates": duplicates})

    ranges = build_version_ranges(ordered_events, default_version, max_seq)
    diffs = _diff_records(state, ranges, ordered_events)

    return {
        "replayed_outputs": state["outputs"],
        "diff": diffs,
        "errors": errors,
        "version_ranges": ranges,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Deterministic session replay and diff attribution.")
    parser.add_argument("session", help="path to session.json")
    parser.add_argument("output", help="path to output.json")
    parser.add_argument("--chunks", type=int, default=1,
                        help="replay events in N contiguous chunks; the "
                             "threaded fold must equal a whole replay")
    args = parser.parse_args(argv)

    with open(args.session, "r", encoding="utf-8") as fh:
        session = json.load(fh)
    report = run(session, chunks=args.chunks)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    main()
