"""Synthetic customer read model with delete thresholds, auditable proof,
and crash-safe snapshot restore.

Layout under ROOT:
  current.json   published state (head)
  staging.json   fully written but not yet published state (crash window)
  v<N>.json      immutable snapshot of each published version

State keeps raw business events plus a separate delete ledger; the read
model is derived by full filtering so deleted entities can never reappear
after a restore (delete thresholds never regress).
"""
import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

EVENT_TYPES = ("customer", "purchase", "delete")
REQUIRED = {
    "customer": ("event", "type", "key", "epoch", "region"),
    "purchase": ("event", "type", "key", "epoch", "purchase", "value"),
    "delete": ("event", "type", "key", "epoch"),
}


class BatchError(Exception):
    """Raised when a batch must be rejected as a whole (payload conflict)."""


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(event):
    return hashlib.sha256(canon(event).encode("utf-8")).hexdigest()


def empty_state():
    return {"version": 0, "customers": [], "purchases": [], "deletes": [], "seen": {}}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")


def recover(root):
    """Roll forward a fully staged commit and reconcile snapshots with the head.

    A crash between staging and publish is safe: the staged state already
    carries the delete ledger, so publishing it cannot resurrect deleted
    entities. Orphan snapshots above the head (from a crash before staging
    completed) are discarded.
    """
    staging = root / "staging.json"
    head = root / "current.json"
    if staging.exists():
        state = load_json(staging)
        snap = root / "v{}.json".format(state["version"])
        if not snap.exists():
            write_json(snap, state)
        os.replace(staging, head)
    head_version = load_json(head)["version"] if head.exists() else 0
    for f in root.glob("v*.json"):
        try:
            n = int(f.stem[1:])
        except ValueError:
            continue
        if n > head_version:
            f.unlink()


def load_current(root):
    head = root / "current.json"
    if head.exists():
        state = load_json(head)
        state.setdefault("seen", {})
        state.setdefault("deletes", [])
        return state
    return empty_state()


def publish(root, state, crash_before_publish):
    """Write the new snapshot and stage the state, then atomically publish."""
    write_json(root / "v{}.json".format(state["version"]), state)
    write_json(root / "staging.json", state)
    if crash_before_publish:
        sys.exit(97)
    os.replace(root / "staging.json", root / "current.json")


def apply_events(state, events):
    """Apply a batch onto a copy of state. Returns (new_state, changed).

    Fully duplicate events are ignored; any payload conflict rejects the
    whole batch. Deletes are appended to the ledger (never removed).
    """
    customers = list(state["customers"])
    purchases = list(state["purchases"])
    deletes = list(state["deletes"])
    seen = dict(state["seen"])
    cust_idx = {(c["key"], c["epoch"]): c for c in customers}
    # purchase IDs are unique within the same type and entity key
    pur_idx = {(p["key"], p["purchase"]): p for p in purchases}
    changed = False
    for event in events:
        etype = event.get("type")
        if etype not in EVENT_TYPES or any(k not in event for k in REQUIRED[etype]):
            raise BatchError("malformed event: {!r}".format(event))
        eid = event["event"]
        digest = event_hash(event)
        if eid in seen:
            if seen[eid] != digest:
                raise BatchError("payload conflict for event {!r}".format(eid))
            continue  # exact duplicate: ignore
        seen[eid] = digest
        record = copy.deepcopy(event)
        if etype == "customer":
            ke = (event["key"], event["epoch"])
            old = cust_idx.get(ke)
            if old is not None:
                if old["region"] != event["region"]:
                    raise BatchError(
                        "customer conflict for key={!r} epoch={!r}".format(*ke))
                continue  # identical business payload: ignore
            customers.append(record)
            cust_idx[ke] = record
            changed = True
        elif etype == "purchase":
            kp = (event["key"], event["purchase"])
            old = pur_idx.get(kp)
            if old is not None:
                if old["epoch"] != event["epoch"] or old["value"] != event["value"]:
                    raise BatchError(
                        "purchase conflict for key={!r} purchase={!r}".format(*kp))
                continue  # identical business payload: ignore
            purchases.append(record)
            pur_idx[kp] = record
            changed = True
        else:  # delete: record the raw operation in the audit ledger
            deletes.append(record)
            changed = True
    return {
        "version": state["version"],
        "customers": customers,
        "purchases": purchases,
        "deletes": deletes,
        "seen": seen,
    }, changed


def delete_thresholds(deletes):
    """Restore threshold per key: a delete masks every epoch <= its own."""
    thresholds = {}
    for d in deletes:
        k, e = d["key"], d["epoch"]
        if k not in thresholds or e > thresholds[k]:
            thresholds[k] = e
    return thresholds


def build_view(state):
    """Full filter + recompute of every read table from the raw state."""
    thresholds = delete_thresholds(state["deletes"])

    def visible(rec):
        t = thresholds.get(rec["key"])
        return t is None or rec["epoch"] > t

    vcust = [c for c in state["customers"] if visible(c)]
    customers = sorted(
        ({"key": c["key"], "epoch": c["epoch"], "region": c["region"]} for c in vcust),
        key=lambda r: (r["key"], r["epoch"]))
    region_of = {(c["key"], c["epoch"]): c["region"] for c in vcust}
    # a purchase is visible only with a visible customer of the same key/epoch
    vpur = [p for p in state["purchases"] if (p["key"], p["epoch"]) in region_of]
    purchases = sorted(
        ({"key": p["key"], "epoch": p["epoch"], "purchase": p["purchase"],
          "value": p["value"]} for p in vpur),
        key=lambda r: (r["key"], r["epoch"], r["purchase"]))
    agg = {}
    for p in vpur:
        region = region_of[(p["key"], p["epoch"])]
        a = agg.setdefault(region, [0, 0])
        a[0] += 1
        a[1] += p["value"]
    totals = [{"region": r, "count": c, "value": v}
              for r, (c, v) in sorted(agg.items())]
    proof = {
        "active_delete_count": len(state["deletes"]),
        "sha256": hashlib.sha256(
            canon([customers, purchases, totals]).encode("utf-8")).hexdigest(),
    }
    return {"version": state["version"], "customers": customers,
            "purchases": purchases, "totals": totals, "proof": proof}


def cmd_apply(root, input_path, crash):
    recover(root)
    state = load_current(root)
    data = load_json(input_path)
    new_state, changed = apply_events(state, data["events"])
    if changed:
        new_state["version"] = state["version"] + 1
        publish(root, new_state, crash)
    return new_state


def cmd_restore(root, version, crash):
    recover(root)
    current = load_current(root)
    if version == 0:
        snap = empty_state()
    else:
        snap_path = root / "v{}.json".format(version)
        if not snap_path.exists():
            print("error: snapshot v{} not found".format(version), file=sys.stderr)
            sys.exit(1)
        snap = load_json(snap_path)
    # rebuild from the old snapshot, then re-align with every current delete
    # threshold: the delete ledger is carried over and can never regress
    deletes = list(current["deletes"])
    seen = dict(snap.get("seen", {}))
    for d in deletes:
        seen[d["event"]] = event_hash(d)
    new_state = {
        "version": current["version"] + 1,
        "customers": list(snap["customers"]),
        "purchases": list(snap["purchases"]),
        "deletes": deletes,
        "seen": seen,
    }
    publish(root, new_state, crash)
    return new_state


def cmd_inspect(root):
    recover(root)
    return load_current(root)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    sub = p.add_subparsers(dest="op", required=True)
    a_apply = sub.add_parser("apply")
    a_apply.add_argument("input", type=Path)
    a_restore = sub.add_parser("restore")
    a_restore.add_argument("version", type=int)
    for a in (a_apply, a_restore):
        a.add_argument("--crash-before-publish", action="store_true")
    sub.add_parser("inspect")
    args = p.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    try:
        if args.op == "apply":
            state = cmd_apply(args.root, args.input, args.crash_before_publish)
        elif args.op == "restore":
            state = cmd_restore(args.root, args.version, args.crash_before_publish)
        else:
            state = cmd_inspect(args.root)
    except BatchError as e:
        print("error: {}".format(e), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(build_view(state), sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
