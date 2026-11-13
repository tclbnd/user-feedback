"""Idempotent batch collector with a recoverable outbox protocol.

Persistence boundaries (all local, all atomic):

  1. after-outbox      : pending records / rejects durably stored
  2. after-sink        : sink commit durable, local confirmation not yet stored
  3. after-checkpoint  : per-partition contiguous checkpoint durable
  4. after-ack         : source acknowledgement durable, cleanup not yet done

Outbox record statuses:
  PENDING   : written locally, never observed as sent
  UNKNOWN   : a previous process may have sent it (crash/restart recovery)
  COMMITTED : sink durably holds key + digest
  CONFLICT  : sink holds the same key with a different digest (never overwritten)
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

CRASH_EXIT = 97
STALE_EXIT = 3
BAD_INPUT_EXIT = 1
DEFAULT_WATERMARK = 1000

PENDING, UNKNOWN, COMMITTED, CONFLICT = "PENDING", "UNKNOWN", "COMMITTED", "CONFLICT"
TERMINAL = (COMMITTED, CONFLICT)
INVALID, DIGEST_CONFLICT = "INVALID", "DIGEST_CONFLICT"


# --------------------------------------------------------------------------- #
# serialization / storage helpers
# --------------------------------------------------------------------------- #
def canonical_digest(value):
    """sha256 over canonical JSON (sort_keys, no ascii escaping, tight sep)."""
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def initial_state():
    return {
        "leases": {},
        "checkpoints": {},
        "acks": {},
        "outbox": [],
        "rejects": [],
        "backpressure": [],
    }


def load_state(path):
    if not Path(path).exists():
        return initial_state()
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    base = initial_state()
    base.update(state)
    for key in ("leases", "checkpoints", "acks"):
        base[key] = {str(k): int(v) for k, v in base[key].items()}
    return base


def save_state(root, state):
    atomic_write(Path(root) / "state.json", json.dumps(state, sort_keys=True) + "\n")


def load_sink(root):
    path = Path(root) / "sink.json"
    if not path.exists():
        return {"items": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_sink(root, sink):
    atomic_write(Path(root) / "sink.json", json.dumps(sink, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# crash injection: only effective boundaries fire, so reruns always progress
# --------------------------------------------------------------------------- #
class CrashController:
    def __init__(self, point):
        self.point = point
        self.fired = False

    def hit(self, boundary, effective):
        if self.fired or self.point != boundary or not effective:
            return
        self.fired = True
        note = json.dumps({"crash": boundary, "exit": CRASH_EXIT})
        sys.stderr.write(note + "\n")
        sys.stderr.flush()
        os._exit(CRASH_EXIT)


# --------------------------------------------------------------------------- #
# read-only view
# --------------------------------------------------------------------------- #
def build_view(state, sink):
    partitions = set(state["leases"]) | set(state["checkpoints"]) | set(state["acks"])
    for rec in state["outbox"]:
        partitions.add(rec["partition"])
    for rec in state["rejects"]:
        partitions.add(rec["partition"])
    reject_view = [
        dict(partition=r["partition"], offset=r["offset"], reason=r["reason"])
        for r in state["rejects"]
    ]
    outbox_view = [
        dict(
            batch=r["batch"],
            partition=r["partition"],
            offset=r["offset"],
            key=r["key"],
            sha256=r["digest"],
            status=r["status"],
        )
        for r in state["outbox"]
    ]
    return {
        "leases": {p: state["leases"].get(p, 0) for p in sorted(partitions)},
        "checkpoints": {
            p: state["checkpoints"].get(p, 0) for p in sorted(partitions)
        },
        "acks": {p: state["acks"].get(p, 0) for p in sorted(partitions)},
        "sink": [
            dict(key=k, sha256=sink["items"][k]["sha256"])
            for k in sorted(sink["items"])
        ],
        "rejects": sorted(
            reject_view, key=lambda r: (r["partition"], r["offset"])
        ),
        "outbox": sorted(
            outbox_view, key=lambda r: (r["partition"], r["offset"])
        ),
        "backpressure": sorted(
            state["backpressure"], key=lambda r: (r["partition"], r["offset"])
        ),
    }


def emit(state, sink):
    print(json.dumps(build_view(state, sink), sort_keys=True, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# lease command
# --------------------------------------------------------------------------- #
def cmd_lease(root, partition, generation):
    state_path = Path(root) / "state.json"
    state = load_state(state_path)
    current = state["leases"].get(partition, 0)
    if generation < current:
        note = json.dumps(
            dict(
                error="STALE_LEASE",
                partition=partition,
                requested=generation,
                current=current,
            )
        )
        sys.stderr.write(note + "\n")
        return STALE_EXIT
    if generation > current:
        state["leases"][partition] = generation
        save_state(root, state)
    emit(state, load_sink(root))
    return 0


# --------------------------------------------------------------------------- #
# apply command
# --------------------------------------------------------------------------- #
def _parse_rows(events):
    """Normalize raw input rows; validate structure and de-duplicate offsets."""
    rows, seen = [], set()
    for row in events:
        if not isinstance(row, dict) or "partition" not in row or "offset" not in row:
            raise ValueError("row missing partition/offset: %r" % (row,))
        partition = str(row["partition"])
        offset = row["offset"]
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 1:
            raise ValueError("bad offset: %r" % (row,))
        ident = (partition, offset)
        if ident in seen:  # source re-delivered the same row inside one batch
            continue
        seen.add(ident)
        if bool(row.get("invalid", False)):
            rows.append(dict(partition=partition, offset=offset, invalid=True))
            continue
        if "key" not in row or "payload" not in row:
            raise ValueError("valid row missing key/payload: %r" % (row,))
        rows.append(
            dict(
                partition=partition,
                offset=offset,
                invalid=False,
                key=str(row["key"]),
                digest=canonical_digest(row["payload"]),
            )
        )
    rows.sort(key=lambda r: (r["partition"], r["offset"]))
    return rows


def _batch_id(rows):
    identity = [
        dict(
            partition=r["partition"],
            offset=r["offset"],
            key=None if r["invalid"] else r["key"],
            digest=None if r["invalid"] else r["digest"],
        )
        for r in rows
    ]
    return "b_" + canonical_digest(identity)[:16]


def cmd_apply(root, input_path, generation, crash_point, watermark):
    crash = CrashController(crash_point)
    state_path = Path(root) / "state.json"
    state = load_state(state_path)
    sink = load_sink(root)

    try:
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        rows = _parse_rows(payload.get("events", []))
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        note = json.dumps(dict(error="BAD_INPUT", detail=str(exc)))
        sys.stderr.write(note + "\n")
        return BAD_INPUT_EXIT

    # --- lease gate: every partition in this batch must own this generation -
    partitions = sorted({r["partition"] for r in rows})
    for partition in partitions:
        if state["leases"].get(partition, 0) != generation:
            note = json.dumps(
                dict(
                    error="STALE_LEASE",
                    partition=partition,
                    requested=generation,
                    current=state["leases"].get(partition, 0),
                )
            )
            sys.stderr.write(note + "\n")
            return STALE_EXIT

    batch_id = _batch_id(rows)

    # --- recovery: anything still PENDING from an interrupted run is now in
    #     an unknown commit state until the sink reconciles it ---------------
    recovered = False
    for rec in state["outbox"]:
        if rec["status"] == PENDING and rec["batch"] == batch_id:
            rec["status"] = UNKNOWN
            recovered = True
    if recovered:
        save_state(root, state)

    outbox_index = {(r["partition"], r["offset"]): r for r in state["outbox"]}
    reject_index = {(r["partition"], r["offset"]): r for r in state["rejects"]}

    # --- audit + watermark backpressure -------------------------------------
    deferred, outbox_added, rejects_added = [], 0, 0
    for row in rows:
        p, off = row["partition"], row["offset"]
        cp = state["checkpoints"].get(p, 0)
        ack = state["acks"].get(p, 0)
        if off <= ack:
            continue  # source re-delivery of an already confirmed row
        if off > cp + watermark:
            deferred.append(
                dict(partition=p, offset=off, reason="BEHIND_WATERMARK")
            )
            continue
        if row["invalid"]:
            if (p, off) not in reject_index:
                rej = dict(partition=p, offset=off, reason=INVALID)
                state["rejects"].append(rej)
                reject_index[(p, off)] = rej
                rejects_added += 1
            continue
        if (p, off) in outbox_index:
            continue  # already bound to a local record; recovery path handles it
        rec = dict(
            batch=batch_id,
            partition=p,
            offset=off,
            key=row["key"],
            digest=row["digest"],
            status=PENDING,
        )
        state["outbox"].append(rec)
        outbox_index[(p, off)] = rec
        outbox_added += 1

    state["backpressure"] = deferred
    if outbox_added or rejects_added or recovered:
        save_state(root, state)
    # recovery reclassification (PENDING -> UNKNOWN) is itself a durable
    # outbox boundary, so crash injection must be able to stop there too
    crash.hit(
        "after-outbox", bool(outbox_added or rejects_added or recovered)
    )

    # --- sink phase: idempotent commits, digest conflicts never overwrite ---
    for rec in sorted(state["outbox"], key=lambda r: (r["partition"], r["offset"])):
        if rec["status"] in TERMINAL:
            continue
        existing = sink["items"].get(rec["key"])
        if existing is not None:
            ident = (rec["partition"], rec["offset"])
            if existing["sha256"] == rec["digest"]:
                rec["status"] = COMMITTED  # duplicate request, no duplicate commit
            else:
                rec["status"] = CONFLICT
                if ident not in reject_index:
                    rej = dict(
                        partition=rec["partition"],
                        offset=rec["offset"],
                        reason=DIGEST_CONFLICT,
                    )
                    state["rejects"].append(rej)
                    reject_index[ident] = rej
            save_state(root, state)
            continue
        # real commit window: sink durable before the local confirmation
        sink["items"][rec["key"]] = dict(
            key=rec["key"], sha256=rec["digest"]
        )
        save_sink(root, sink)
        crash.hit("after-sink", True)  # local record still says PENDING/UNKNOWN
        rec["status"] = COMMITTED
        save_state(root, state)

    # --- checkpoint phase: contiguous, fully resolved prefix only -----------
    resolved = {}
    for rec in state["outbox"]:
        if rec["status"] in TERMINAL:
            resolved.setdefault(rec["partition"], set()).add(rec["offset"])
    for rej in state["rejects"]:
        resolved.setdefault(rej["partition"], set()).add(rej["offset"])

    cp_advanced = False
    for p in partitions:
        cp = state["checkpoints"].get(p, 0)
        nxt = cp + 1
        while nxt in resolved.get(p, set()):
            nxt += 1
        new_cp = nxt - 1
        if new_cp > cp:
            state["checkpoints"][p] = new_cp
            cp_advanced = True
    if cp_advanced:
        save_state(root, state)
    crash.hit("after-checkpoint", cp_advanced)

    # --- source acknowledgement: never beyond the checkpoint ----------------
    ack_advanced = False
    for p in partitions:
        target = state["checkpoints"].get(p, 0)
        if state["acks"].get(p, 0) < target:
            state["acks"][p] = target
            ack_advanced = True
    if ack_advanced:
        save_state(root, state)
    crash.hit("after-ack", ack_advanced)

    # --- cleanup: only records covered by a durable acknowledgement ---------
    before = len(state["outbox"])
    state["outbox"] = [
        rec
        for rec in state["outbox"]
        if rec["offset"] > state["acks"].get(rec["partition"], 0)
    ]
    if len(state["outbox"]) != before:
        save_state(root, state)

    emit(state, sink)
    return 0


def cmd_inspect(root):
    state = load_state(Path(root) / "state.json")
    emit(state, load_sink(root))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Recoverable batch collector")
    parser.add_argument("root", type=Path)
    sub = parser.add_subparsers(dest="op", required=True)

    lease_p = sub.add_parser("lease")
    lease_p.add_argument("partition")
    lease_p.add_argument("generation", type=int)

    apply_p = sub.add_parser("apply")
    apply_p.add_argument("input", type=Path)
    apply_p.add_argument("--lease", type=int, required=True)
    apply_p.add_argument(
        "--crash",
        choices=["after-outbox", "after-sink", "after-checkpoint", "after-ack"],
    )
    apply_p.add_argument("--watermark", type=int, default=DEFAULT_WATERMARK)

    sub.add_parser("inspect")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)

    if args.op == "lease":
        code = cmd_lease(args.root, args.partition, args.generation)
    elif args.op == "apply":
        code = cmd_apply(
            args.root,
            args.input,
            args.lease,
            args.crash,
            args.watermark,
        )
    else:
        code = cmd_inspect(args.root)
    sys.exit(code)


if __name__ == "__main__":
    main()
