#!/usr/bin/env python3
"""Fold an event stream into aggregate state.

Old-version events are migrated through a registered upcaster chain
*before* folding; a missing migration path is an explicit, seq-located
error (recorded in `rejects`), never a silent skip.

CLI (unchanged):  python3 replay.py events.json output.json \
                      [--snapshot snap.json] [--fail-after n]
"""
import json
import sys
from decimal import Decimal, InvalidOperation

# Canonical payload version that the folder understands.  Anything older
# is migrated up to this version by the upcaster chain first.
TARGET_VERSION = 3


class UpcastError(Exception):
    """Raised when an event cannot be migrated to the target version."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Upcaster registry: (event_type, from_version) -> fn(data) -> data at v+1
# Each upcaster validates the semantics of *its* source version, so a
# version's invariants are enforced exactly once, at migration time.
# ---------------------------------------------------------------------------
UPCASTERS = {}


def register(event_type, from_version):
    def deco(fn):
        UPCASTERS[(event_type, from_version)] = fn
        return fn
    return deco


@register("credit", 1)
@register("debit", 1)
def _upcast_1_to_2(d):
    # v1: data.amount is integer cents; currency did not exist -> CNY.
    if "amount" not in d:
        raise UpcastError("missing-amount")
    return {"cents": int(d["amount"]), "currency": "CNY"}


@register("credit", 2)
@register("debit", 2)
def _upcast_2_to_3(d):
    # v2: data.cents is cents; currency is required and must be CNY.
    if "cents" not in d:
        raise UpcastError("missing-cents")
    currency = d.get("currency")
    if currency is None:
        raise UpcastError("missing-currency")
    if currency != "CNY":
        raise UpcastError("unsupported-currency")
    cents = int(d["cents"])
    sign = "-" if cents < 0 else ""
    yuan, rem = divmod(abs(cents), 100)
    # v3 shape: amount is a decimal-yuan string.
    return {"amount": "%s%d.%02d" % (sign, yuan, rem), "currency": currency}


def migrate(event_type, version, data):
    """Migrate `data` from `version` up to TARGET_VERSION.

    Returns (migrated_data, path) where path lists every version visited,
    e.g. [1, 2, 3].  Raises UpcastError when a chain link is missing or a
    version's invariants are violated.
    """
    path = [version]
    v = version
    d = data
    while v != TARGET_VERSION:
        up = UPCASTERS.get((event_type, v))
        if up is None:
            raise UpcastError("no-upcast-path:v%s->v%s" % (v, TARGET_VERSION))
        d = up(d)
        v = v + 1
        path.append(v)
    return d, path


def parse_v3_cents(d):
    """v3 semantics: data.amount is a decimal-yuan string -> integer cents."""
    amount = d.get("amount")
    if not isinstance(amount, str):
        raise UpcastError("bad-amount")
    try:
        cents = Decimal(amount) * 100
    except InvalidOperation:
        raise UpcastError("bad-amount")
    if cents != cents.to_integral_value():
        raise UpcastError("bad-amount")
    return int(cents)


# ---------------------------------------------------------------------------
# Replayer: all mutable fold state lives on the instance, so concurrent
# replays of different streams never interfere with each other.
# ---------------------------------------------------------------------------
class Replayer:
    def __init__(self):
        self.state = {"balance_cents": 0, "name": "", "applied": 0}
        self.rejects = []
        self.applied_by_version = {}
        self.migrations = []
        self.last_seq = -1
        self._name_seq = -1  # seq of the last applied rename (for merging)

    # -- snapshot ---------------------------------------------------------
    def to_snapshot(self):
        # Only *migrated* results are cached: the state is always the fold
        # of canonical (target-version) events, never of raw payloads.
        return {
            "last_seq": self.last_seq,
            "state": dict(self.state),
            "rejects": list(self.rejects),
            "applied_by_version": dict(self.applied_by_version),
            "migrations": list(self.migrations),
            "name_seq": self._name_seq,
        }

    @classmethod
    def from_snapshot(cls, snap):
        r = cls()
        r.state = dict(snap["state"])
        r.last_seq = snap["last_seq"]
        r.rejects = list(snap.get("rejects", []))
        r.applied_by_version = dict(snap.get("applied_by_version", {}))
        r.migrations = list(snap.get("migrations", []))
        r._name_seq = snap.get("name_seq", -1)
        return r

    # -- fold ---------------------------------------------------------------
    def apply_one(self, ev):
        seq = ev["seq"]
        t = ev.get("type")
        v = ev.get("v")
        d = ev.get("data") or {}
        if t not in ("credit", "debit", "rename"):
            self.rejects.append({"seq": seq, "reason": "unknown-type"})
            return
        if t == "rename":  # rename is supported at any version
            if "name" not in d:
                self.rejects.append({"seq": seq, "reason": "missing-name"})
                return
            self.state["name"] = d["name"]
            self._name_seq = seq
            self._applied(seq, t, v, [v])
            return
        try:
            migrated, path = migrate(t, v, d)
            cents = parse_v3_cents(migrated)
        except UpcastError as e:
            self.rejects.append({"seq": seq, "reason": e.reason})
            return
        if t == "credit":
            self.state["balance_cents"] += cents
        else:
            self.state["balance_cents"] -= cents
        self._applied(seq, t, v, path)

    def _applied(self, seq, t, v, path):
        self.state["applied"] += 1
        key = str(v)
        self.applied_by_version[key] = self.applied_by_version.get(key, 0) + 1
        self.migrations.append({"seq": seq, "type": t, "path": path})

    def fold(self, events, fail_after=None, on_checkpoint=None):
        for i, ev in enumerate(events):
            if fail_after is not None and i >= fail_after:
                if on_checkpoint is not None:
                    on_checkpoint(self)  # persist crash-recovery point
                sys.exit(3)
            self.apply_one(ev)
            self.last_seq = ev["seq"]

    # -- merge (for parallel replay of split streams) -----------------------
    def merge(self, other):
        """Merge a replayer that folded *disjoint, later* events into self."""
        self.state["balance_cents"] += other.state["balance_cents"]
        self.state["applied"] += other.state["applied"]
        if other._name_seq > self._name_seq:
            self.state["name"] = other.state["name"]
            self._name_seq = other._name_seq
        self.rejects = sorted(self.rejects + other.rejects,
                              key=lambda r: r["seq"])
        for k, n in other.applied_by_version.items():
            self.applied_by_version[k] = self.applied_by_version.get(k, 0) + n
        self.migrations = sorted(self.migrations + other.migrations,
                                 key=lambda m: m["seq"])
        self.last_seq = max(self.last_seq, other.last_seq)
        return self

    def report(self):
        return {
            "state": self.state,
            "rejects": self.rejects,
            "applied_by_version": self.applied_by_version,
            "migrations": self.migrations,
        }


def main():
    req_in, out_p = sys.argv[1], sys.argv[2]
    snap_p = None
    fail_after = None
    a = sys.argv[3:]
    for i, x in enumerate(a):
        if x == "--snapshot":
            snap_p = a[i + 1]
        if x == "--fail-after":
            fail_after = int(a[i + 1])

    events = json.load(open(req_in))["events"]

    r = Replayer()
    if snap_p:
        try:
            r = Replayer.from_snapshot(json.load(open(snap_p)))
        except FileNotFoundError:
            pass

    todo = [e for e in events if e["seq"] > r.last_seq]

    def checkpoint(rep):
        if snap_p:
            json.dump(rep.to_snapshot(), open(snap_p, "w"))

    r.fold(todo, fail_after=fail_after, on_checkpoint=checkpoint)

    json.dump(r.report(), open(out_p, "w"))
    if snap_p:
        json.dump(r.to_snapshot(), open(snap_p, "w"))


if __name__ == "__main__":
    main()
