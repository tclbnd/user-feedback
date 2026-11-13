"""Telemetry cumulative-counter replay engine.

A deterministic, restartable, versioned interpreter for monotonically
increasing device counters.  Pairs of adjacent samples are classified as
normal / wrap (same generation, provable modulo increment), reset (explicit
boot generation change), gap (out of observability window) or unknown
(ambiguous jump that could be a wrap or a reset).  Samples may arrive late
and out of order; inserting a point between two existing points withdraws the
spanning interval and publishes the two replacement intervals as a revocable
change batch so hourly rollups can be re-applied identically every time.

State is persisted atomically (temp file + os.replace) and keeps the read
cursor, per-device ordered samples, the current interval map and the revision
journal.  Nothing is ever implicitly allocated to an hour: only provable
increments are attributed, always to the hour of the interval's end sample.
"""
import argparse
import json
import os
import sys
import tempfile
from bisect import bisect_left
from pathlib import Path

EXIT_CRASH = 97

KINDS = ("normal", "wrap", "reset", "gap", "unknown")
KNOWN_KINDS = ("normal", "wrap")


def classify(prev, cur, cfg):
    """Classify the interval from sample prev to sample cur.

    Independent of arrival order: only the two endpoints and the device
    configuration matter.  Returns (kind, delta); delta is None whenever the
    true increment is not observable.
    """
    mod = 1 << cfg["bits"]
    span = cur["t"] - prev["t"]
    if cur["boot"] != prev["boot"]:
        return "reset", None
    if span > cfg["max_gap"]:
        return "gap", None
    budget = cfg["max_rate"] * span
    mod_delta = (cur["value"] - prev["value"]) % mod
    if budget >= mod or mod_delta > budget:
        # A wrap and a reset are both consistent with the observations; the
        # source rules cannot bound the true increment, so it stays unknown.
        return "unknown", None
    if cur["value"] < prev["value"]:
        # Same generation, bounded window, decrement can only be modulo wrap.
        return "wrap", mod_delta
    return "normal", mod_delta


def make_interval(prev, cur, cfg):
    kind, delta = classify(prev, cur, cfg)
    return {
        "device": prev["device"],
        "start": prev["t"],
        "end": cur["t"],
        "kind": kind,
        "delta": delta,
    }


def ikey(iv):
    return iv["device"], iv["start"], iv["end"]


def new_state():
    # Each device keeps an ordered point list and a parallel timestamp list
    # because bisect(key=...) only exists on Python 3.10+.
    return {
        "cursor": 0,
        "samples": {},
        "times": {},
        "intervals": {},
        "changes": [],
    }


def load_state(path):
    if not path.exists():
        return new_state()
    raw = json.loads(path.read_text())
    state = new_state()
    state["cursor"] = raw["cursor"]
    state["samples"] = raw["samples"]
    state["times"] = {name: list(ts) for name, ts in raw["times"].items()}
    state["changes"] = raw["changes"]
    for iv in raw["intervals"]:
        state["intervals"][ikey(iv)] = iv
    return state


def fold_intervals(change_batches):
    """Replay change batches to reconstruct an interval map at a version.

    Removes (keyed by device/start/end) are applied before upserts inside each
    batch, which makes the fold total and order-independent across different
    apply batching of the same logical input.
    """
    intervals = {}
    for batch in change_batches:
        for iv in batch["remove"]:
            intervals.pop(ikey(iv), None)
        for iv in batch["upsert"]:
            intervals[ikey(iv)] = iv
    return intervals


def build_hourly(intervals):
    """Attribute provable deltas to the hour of the END sample only.

    Known zero increments are kept (they are observations); unknown
    intervals never touch the rollup and no cross-hour splitting occurs, so
    unobservable data is never implicitly assigned to an hour.
    """
    acc = {}
    for iv in intervals.values():
        if iv["kind"] in KNOWN_KINDS:
            key = (iv["device"], iv["end"] // 3600)
            acc[key] = acc.get(key, 0) + iv["delta"]
    return [
        {"device": d, "hour": h, "delta": acc[(d, h)]}
        for (d, h) in sorted(acc)
    ]


def sorted_intervals(intervals):
    return [intervals[k] for k in sorted(intervals)]


def build_view(state):
    return {
        "version": state["cursor"],
        "intervals": sorted_intervals(state["intervals"]),
        "hourly": build_hourly(state["intervals"]),
        "changes": state["changes"],
    }


def _diff_intervals(before, after):
    """Deterministic withdraw/upsert pair for two adjacent interval sets."""
    removed, upserted = [], []
    for key, iv in before.items():
        if key not in after or after[key] != iv:
            removed.append(iv)
    for key, iv in after.items():
        if key not in before or before[key] != iv:
            upserted.append(iv)
    return sorted(removed, key=ikey), sorted(upserted, key=ikey)


def ingest_sample(state, sample, cfg):
    """Fold one logical sample; return the change batch, or None on no-op.

    Same (device, t): greatest rev wins.  Identical duplicate records have no
    side effect; same rev with different content is a hard conflict.
    """
    name = sample["device"]
    points = state["samples"].setdefault(name, [])
    times = state["times"].setdefault(name, [])
    idx = bisect_left(times, sample["t"])
    existing = (
        points[idx]
        if idx < len(points) and points[idx]["t"] == sample["t"]
        else None
    )

    if existing is not None:
        if sample["rev"] < existing["rev"]:
            return None  # stale revision, already superseded
        if sample["rev"] == existing["rev"]:
            if (
                existing["value"] == sample["value"]
                and existing["boot"] == sample["boot"]
            ):
                return None  # idempotent duplicate
            raise ValueError(
                "conflicting sample for device %r at t=%d rev=%d: %r vs %r"
                % (name, sample["t"], sample["rev"], existing, sample)
            )
        before = {}
        if idx > 0:
            iv = make_interval(points[idx - 1], existing, cfg)
            before[ikey(iv)] = iv
        if idx + 1 < len(points):
            iv = make_interval(existing, points[idx + 1], cfg)
            before[ikey(iv)] = iv
        points[idx] = sample
        after = {}
        if idx > 0:
            iv = make_interval(points[idx - 1], sample, cfg)
            after[ikey(iv)] = iv
        if idx + 1 < len(points):
            iv = make_interval(sample, points[idx + 1], cfg)
            after[ikey(iv)] = iv
    else:
        before = {}
        if 0 < idx < len(points):
            iv = make_interval(points[idx - 1], points[idx], cfg)
            before[ikey(iv)] = iv
        points.insert(idx, sample)
        times.insert(idx, sample["t"])
        after = {}
        if idx > 0:
            iv = make_interval(points[idx - 1], sample, cfg)
            after[ikey(iv)] = iv
        if idx + 1 < len(points):
            iv = make_interval(sample, points[idx + 1], cfg)
            after[ikey(iv)] = iv

    remove, upsert = _diff_intervals(before, after)
    for iv in remove:
        state["intervals"].pop(ikey(iv), None)
    for iv in upsert:
        state["intervals"][ikey(iv)] = iv
    if not remove and not upsert:
        return None
    # Version is stamped by the caller once the cursor has advanced.
    return {"version": None, "remove": remove, "upsert": upsert}


def _atomic_persist(db, state):
    db.parent.mkdir(parents=True, exist_ok=True)
    on_disk = dict(state)
    on_disk["intervals"] = sorted_intervals(state["intervals"])
    fd, tmp = tempfile.mkstemp(
        prefix=db.name + ".", suffix=".tmp", dir=str(db.parent)
    )
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(on_disk, fh, sort_keys=True, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, db)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply(source, db, upto=None, crash=False):
    data = json.loads(source.read_text())
    devices = data["devices"]
    samples = data["samples"]
    state = load_state(db)

    total = len(samples)
    target = total if upto is None else min(upto, total)
    if upto is not None and upto < 0:
        raise ValueError("--upto must be non-negative")
    if target < state["cursor"]:
        raise ValueError(
            "cursor already at %d, cannot rewind to %d" % (state["cursor"], target)
        )

    for i in range(state["cursor"], target):
        sample = samples[i]
        if sample["device"] not in devices:
            raise ValueError("unknown device %r" % sample["device"])
        batch = ingest_sample(state, sample, devices[sample["device"]])
        # A logical version is the consumed-prefix length after accepting
        # this sample; the batch carries that new cursor value.
        state["cursor"] = i + 1
        if batch is not None:
            batch["version"] = state["cursor"]
            state["changes"].append(batch)

    if crash:
        # Visible commit point deliberately skipped: the on-disk snapshot
        # remains untouched and a normal re-run resumes to the same truth.
        sys.stderr.write("simulated crash before publish\n")
        sys.exit(EXIT_CRASH)

    _atomic_persist(db, state)
    return build_view(state)


def inspect(db, version=None):
    state = load_state(db)
    if version is None:
        return build_view(state)
    if version < 0 or version > state["cursor"]:
        raise ValueError(
            "version %d not available (current %d)" % (version, state["cursor"])
        )
    if version == state["cursor"]:
        return build_view(state)
    batches = [b for b in state["changes"] if b["version"] <= version]
    intervals = fold_intervals(batches)
    return {
        "version": version,
        "intervals": sorted_intervals(intervals),
        "hourly": build_hourly(intervals),
        "changes": batches,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sync = sub.add_parser("apply", help="consume a prefix of the input stream")
    sync.add_argument("source", type=argparse.FileType("r"))
    sync.add_argument("state", type=Path)
    sync.add_argument("--upto", type=int)
    sync.add_argument("--crash-before-publish", action="store_true")
    view = sub.add_parser("inspect", help="read current or historical state")
    view.add_argument("state", type=Path)
    view.add_argument("--version", type=int)
    args = parser.parse_args()
    try:
        if args.action == "apply":
            out = apply(
                Path(args.source.name),
                args.state,
                args.upto,
                args.crash_before_publish,
            )
        else:
            out = inspect(args.state, args.version)
    except ValueError as exc:
        sys.stderr.write("error: %s\n" % exc)
        sys.exit(1)
    print(json.dumps(out, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
