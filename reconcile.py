"""Cross-partition entry reconciliation with an exact integer ledger.

All amounts are converted into the smallest unit of each currency (integer
minor units, never floats). Entries, revisions (highest rev wins per id) and
reversals are linked into source lineage, paired state is maintained across
partitions, and every published report is versioned, immutable and produced
atomically so a crash before publication leaves the previous report intact.

Subcommands:
  apply SOURCE [--crash-before-publish]  ingest one batch, republish report
  inspect [--version N]                   view a published report
  trace TXN                               drill a transaction down to raw rows
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# --- exception codes -------------------------------------------------------
BAD_AMOUNT = "BAD_AMOUNT"
REVERSAL_TARGET = "REVERSAL_TARGET"
REVERSAL_MISMATCH = "REVERSAL_MISMATCH"
REVERSAL_EXCESS = "REVERSAL_EXCESS"

ENTRY_FIELDS = ("id", "rev", "txn", "date", "amount", "side",
                "account", "currency", "source", "line")
SIDES = ("debit", "credit")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
AMOUNT_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")


class UserError(Exception):
    """Fatal input/usage error; reported on stderr with exit code 2."""


# --- exact decimal handling ------------------------------------------------
def to_minor(amount, scale):
    """Convert a non-negative fixed decimal string to integer minor units.

    Raises ValueError on any value that cannot be represented exactly in the
    currency's smallest unit (bad syntax, exponent, too many fraction digits).
    """
    if not isinstance(amount, str) or not AMOUNT_RE.match(amount):
        raise ValueError("amount must be a non-negative plain decimal string")
    whole, _, frac = amount.partition(".")
    if len(frac) > scale:
        raise ValueError("amount has more fraction digits than currency scale")
    return int(whole) * (10 ** scale) + (
        int(frac.ljust(scale, "0")) if scale else 0)


def _valid_date(value):
    if not isinstance(value, str) or not DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _validate_entry(e):
    if not isinstance(e, dict):
        raise UserError("each entry must be an object")
    missing = [f for f in ENTRY_FIELDS if f not in e]
    if missing:
        raise UserError("entry missing required field(s): " + ",".join(missing))
    if not isinstance(e["id"], str) or not e["id"]:
        raise UserError("entry id must be a non-empty string")
    if not isinstance(e["rev"], int) or isinstance(e["rev"], bool) or e["rev"] < 1:
        raise UserError("entry rev must be a positive integer: " + repr(e.get("id")))
    if not isinstance(e["txn"], str) or not e["txn"]:
        raise UserError("entry txn must be a non-empty string")
    if not _valid_date(e["date"]):
        raise UserError("entry " + e["id"] + " has invalid date " + repr(e["date"]))
    if e["side"] not in SIDES:
        raise UserError("entry " + e["id"] + " has invalid side " + repr(e["side"]))
    if not isinstance(e["account"], str) or not e["account"]:
        raise UserError("entry " + e["id"] + " has invalid account")
    if not isinstance(e["currency"], str) or not e["currency"]:
        raise UserError("entry " + e["id"] + " has invalid currency")
    if not isinstance(e["source"], str) or not e["source"]:
        raise UserError("entry " + e["id"] + " has invalid source")
    if not isinstance(e["line"], int) or isinstance(e["line"], bool) or e["line"] < 1:
        raise UserError("entry " + e["id"] + " has invalid source line")
    if "void" in e and not isinstance(e["void"], bool):
        raise UserError("entry " + e["id"] + ": void must be boolean")
    if "reverses" in e and not isinstance(e["reverses"], str):
        raise UserError("entry " + e["id"] + ": reverses must be an entry id string")


# --- atomic storage --------------------------------------------------------
def atomic_write(path, text):
    """Write text to path atomically (temp file in the same dir, fsync, rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dump_json(obj):
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_store(root):
    store_dir = Path(root) / "store"
    return {
        "currency_scale": load_json(store_dir / "currency_scale.json", {}),
        "entries": load_json(store_dir / "entries.json", {}),
        "batches": load_json(store_dir / "batches.json", []),
    }


# --- batch ingestion -------------------------------------------------------
def _content_hash(entry):
    """Stable hash of the reconciliation-relevant content of one entry row."""
    payload = dict((k, entry[k]) for k in ENTRY_FIELDS if k in entry)
    if entry.get("void"):
        payload["void"] = True
    if "reverses" in entry:
        payload["reverses"] = entry["reverses"]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def ingest_batch(store, batch, raw_bytes):
    """Merge one parsed batch into the in-memory store.

    Returns (changed, fingerprint). Raises UserError on malformed input or a
    same-rev content conflict.
    """
    if not isinstance(batch, dict) or "entries" not in batch:
        raise UserError("batch must be an object with an 'entries' array")
    scale_in = batch.get("currency_scale", {})
    if not isinstance(scale_in, dict) or not all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
            and 0 <= v <= 18 for k, v in scale_in.items()):
        raise UserError("currency_scale must map currency codes to 0..18 integers")

    fingerprint = hashlib.sha256(raw_bytes).hexdigest()
    if fingerprint in store["batches"]:
        return False, fingerprint  # identical re-delivery: idempotent no-op

    scale = store["currency_scale"]
    for cur, sc in scale_in.items():
        if cur in scale and scale[cur] != sc:
            raise UserError(
                "currency " + cur + " scale conflict: stored "
                + str(scale[cur]) + ", batch says " + str(sc))
        scale[cur] = sc

    entries = store["entries"]
    changed = False
    for e in batch["entries"]:
        _validate_entry(e)
        eid = e["id"]
        rec = {
            "id": eid,
            "rev": e["rev"],
            "txn": e["txn"],
            "date": e["date"],
            "amount": e["amount"],
            "side": e["side"],
            "account": e["account"],
            "currency": e["currency"],
            "source": e["source"],
            "line": e["line"],
            "void": bool(e.get("void", False)),
            "reverses": e.get("reverses"),
            "content_hash": _content_hash(e),
        }
        # Exact integer conversion; failures are recorded, never fatal.
        if rec["currency"] not in scale:
            rec["minor"] = None
            rec["error"] = BAD_AMOUNT
        else:
            try:
                rec["minor"] = to_minor(rec["amount"], scale[rec["currency"]])
                rec["error"] = None
            except ValueError:
                rec["minor"] = None
                rec["error"] = BAD_AMOUNT

        old = entries.get(eid)
        if old is None:
            entries[eid] = rec
            changed = True
        elif e["rev"] < old["rev"]:
            continue  # stale revision: highest rev wins
        elif e["rev"] == old["rev"]:
            if rec["content_hash"] != old["content_hash"]:
                raise UserError(
                    "entry " + eid + " rev " + str(e["rev"])
                    + " re-delivered with different content")
            continue  # duplicate of the effective version: idempotent
        else:
            entries[eid] = rec  # newer revision supersedes older one
            changed = True

    return changed, fingerprint


# --- core reconciliation ---------------------------------------------------
def _signed_minor(rec):
    """Signed movement of a *valid* entry: debit positive, credit negative."""
    return rec["minor"] if rec["side"] == "debit" else -rec["minor"]


def compute_report(store):
    """Rebuild the whole report from the immutable entry store.

    Pure function: same store in -> byte-identical report out, regardless of
    batch arrival order.
    """
    entries = store["entries"]
    exceptions = []
    valid = {}          # id -> record participating in the ledger
    txn_currencies = defaultdict(set)
    txn_members = defaultdict(set)

    # Step 1: amounts and voids. Iteration is keyed by id so the result is
    # independent of batch/entry arrival order.
    for eid in sorted(entries):
        rec = entries[eid]
        txn_members[rec["txn"]].add(eid)
        if rec["error"] == BAD_AMOUNT:
            exceptions.append({
                "id": eid, "code": BAD_AMOUNT,
                "source": rec["source"], "line": rec["line"]})
            continue
        if rec["void"]:
            continue  # voided rows never participate; raw row stays in store
        valid[eid] = rec
        txn_currencies[rec["txn"]].add(rec["currency"])

    # Step 2: reversals in stable (date, id) order; each is validated against
    # the remaining (not yet consumed) amount of its non-void target.
    consumed = defaultdict(int)        # target id -> minor already reversed
    valid_reversals = []
    for eid in sorted(valid, key=lambda x: (valid[x]["date"], x)):
        rec = valid[eid]
        target_id = rec["reverses"]
        if target_id is None:
            continue
        target = valid.get(target_id)
        if target is None or target.get("reverses") is not None:
            # Missing target may simply be in a later partition; a voided or
            # reversal target can never be reversed.
            code = REVERSAL_TARGET
        elif (rec["currency"] != target["currency"]
              or rec["account"] != target["account"]
              or rec["side"] == target["side"]):
            code = REVERSAL_MISMATCH
        elif rec["date"] < target["date"]:
            code = REVERSAL_MISMATCH
        elif target_id == eid or consumed[target_id] + rec["minor"] > target["minor"]:
            code = REVERSAL_EXCESS
        else:
            code = None
        if code is not None:
            exceptions.append({
                "id": eid, "code": code,
                "source": rec["source"], "line": rec["line"]})
            continue
        consumed[target_id] += rec["minor"]
        valid_reversals.append(eid)

    # Step 3: signed integer movements (originals on their own date, valid
    # reversals land on their own date — a late reversal therefore posts into
    # the later period while the original entry remains queryable).
    # A valid reversal's posted side is already opposite its target, so its
    # normal debit/credit sign is used directly; no extra sign flip.
    balance = defaultdict(int)        # (account, currency) -> minor
    daily = defaultdict(int)          # (date, account, currency) -> minor
    monthly = defaultdict(int)        # (month, account, currency) -> minor
    txn_net = defaultdict(int)        # (txn, currency) -> minor
    moves = {}                        # id -> signed movement (lineage/corrections)

    for eid in sorted(valid):
        rec = valid[eid]
        if rec["reverses"] is not None and eid not in valid_reversals:
            continue  # failed reversal: excluded from every balance
        mv = _signed_minor(rec)
        moves[eid] = mv
        balance[(rec["account"], rec["currency"])] += mv
        daily[(rec["date"], rec["account"], rec["currency"])] += mv
        monthly[(rec["date"][:7], rec["account"], rec["currency"])] += mv
        txn_net[(rec["txn"], rec["currency"])] += mv

    balances_out = [
        dict(account=a, currency=c, minor=balance[(a, c)])
        for (a, c) in sorted(balance)]
    daily_out = [
        dict(date=d, account=a, currency=c, minor=daily[(d, a, c)])
        for (d, a, c) in sorted(daily)]
    monthly_out = [
        dict(month=m, account=a, currency=c, minor=monthly[(m, a, c)])
        for (m, a, c) in sorted(monthly)]

    # Step 4: per-transaction pairing; currencies of one txn never offset.
    unbalanced = []
    for (t, c) in sorted(txn_net):
        if txn_net[(t, c)] != 0:
            unbalanced.append(dict(txn=t, currency=c, minor=txn_net[(t, c)]))

    unbalanced_set = set((u["txn"], u["currency"]) for u in unbalanced)
    # Currencies seen on member rows even when no row participates (e.g. every
    # row is voided or BAD_AMOUNT), so such transactions stay explainable.
    member_ccy = defaultdict(set)
    for eid, rec in entries.items():
        member_ccy[rec["txn"]].add(rec["currency"])

    transactions = []
    for t in sorted(txn_members):
        currencies = sorted(txn_currencies.get(t, set()))
        txn_lines = [
            dict(currency=c,
                 minor=txn_net.get((t, c), 0),
                 status=("unbalanced" if (t, c) in unbalanced_set
                         else "balanced"))
            for c in currencies]
        for c in sorted(member_ccy[t]):
            if c not in currencies:
                txn_lines.append(dict(currency=c, minor=0,
                                      status="no_effective_entry"))
        transactions.append(dict(
            txn=t,
            cross_currency=len(currencies) > 1,
            currencies=txn_lines))

    cross = sorted(t for t in txn_members
                   if len(txn_currencies.get(t, set())) > 1)

    exceptions.sort(key=lambda x: (x["id"], x["code"]))

    lineage = []
    for eid in sorted(valid, key=lambda x: (valid[x]["txn"], x)):
        rec = valid[eid]
        if rec["reverses"] is not None and eid not in valid_reversals:
            continue
        lineage.append(dict(
            txn=rec["txn"], id=eid, rev=rec["rev"],
            source=rec["source"], line=rec["line"]))

    return {
        "balances": balances_out,
        "daily": daily_out,
        "monthly": monthly_out,
        "transactions": transactions,
        "unbalanced": unbalanced,
        "cross_currency_transactions": cross,
        "exceptions": exceptions,
        "lineage": lineage,
        "_moves": moves,  # internal: stripped before publication
    }


def build_corrections(previous_moves, current_moves, entries):
    """Entry-level revision diff between two published versions."""
    out = []
    for eid in sorted(set(previous_moves) | set(current_moves)):
        old = previous_moves.get(eid, 0)
        new = current_moves.get(eid, 0)
        if old == new:
            continue
        if eid not in entries:
            kind = "REMOVED"
        elif eid not in previous_moves:
            kind = "NEW"
        else:
            kind = "CHANGED"
        rec = entries.get(eid)
        out.append(dict(
            id=eid,
            kind=kind,
            minor_before=old,
            minor_after=new,
            delta=new - old,
            txn=rec["txn"] if rec else None,
            source=rec["source"] if rec else None,
            line=rec["line"] if rec else None))
    return out


def public_report(version, report, corrections):
    return {
        "version": version,
        "balances": report["balances"],
        "daily": report["daily"],
        "monthly": report["monthly"],
        "transactions": report["transactions"],
        "unbalanced": report["unbalanced"],
        "cross_currency_transactions": report["cross_currency_transactions"],
        "exceptions": report["exceptions"],
        "lineage": report["lineage"],
        "corrections": corrections,
    }


# --- publication -----------------------------------------------------------
def publish(root, store, report, version, corrections, crash_before_publish):
    """Durable, ordered, atomic publication.

    Immutable per-version artefacts are written first, the mutable entry
    store next, and the 'latest' pointer last; the pointer flip is the
    commit point. --crash-before-publish exits 97 before any of this happens.
    """
    root = Path(root)
    store_dir = root / "store"
    reports_dir = root / "reports"
    pub = public_report(version, report, corrections)

    if crash_before_publish:
        sys.stderr.write(
            "simulated crash before publishing version " + str(version) + "\n")
        sys.exit(97)

    atomic_write(reports_dir / ("v" + str(version) + ".json"), dump_json(pub))
    atomic_write(reports_dir / ("moves-v" + str(version) + ".json"),
                 dump_json(report["_moves"]))
    # Point-in-time store snapshot so an immutable historical version can be
    # re-verified independently after later batches have arrived.
    snapshot = {
        "currency_scale": store["currency_scale"],
        "entries": store["entries"],
        "batches": list(store["batches"]),
    }
    atomic_write(reports_dir / ("store-v" + str(version) + ".json"),
                 dump_json(snapshot))
    atomic_write(store_dir / "currency_scale.json",
                 dump_json(store["currency_scale"]))
    atomic_write(store_dir / "entries.json", dump_json(store["entries"]))
    atomic_write(store_dir / "batches.json", dump_json(store["batches"]))
    atomic_write(reports_dir / "latest.json", dump_json(pub))  # commit point
    return pub


# --- read-only drill-down --------------------------------------------------
def trace_transaction(store, txn):
    entries = store["entries"]
    members = sorted((eid for eid, rec in entries.items() if rec["txn"] == txn),
                     key=lambda x: (entries[x]["date"], x))
    if not members:
        raise UserError("unknown transaction: " + repr(txn))

    # Re-run the ledger rules over the whole store so the drill-down always
    # agrees with the published report.
    report = compute_report(store)
    live_moves = report["_moves"]
    per_currency = defaultdict(int)
    rows = []
    for eid in members:
        rec = entries[eid]
        row = dict(
            id=eid,
            rev=rec["rev"],
            date=rec["date"],
            account=rec["account"],
            currency=rec["currency"],
            side=rec["side"],
            amount=rec["amount"],
            minor=rec["minor"],
            void=rec["void"],
            reverses=rec["reverses"],
            source=rec["source"],
            line=rec["line"],
            effective=eid in live_moves)
        if rec["error"]:
            row["effective"] = False
            row["reason"] = rec["error"]
        elif rec["void"]:
            row["reason"] = "VOIDED"
        elif not row["effective"] and rec["reverses"] is not None:
            found = next((x for x in report["exceptions"] if x["id"] == eid), None)
            row["reason"] = found["code"] if found else "REJECTED"
        else:
            row["reason"] = "OK"
            row["effective_minor"] = _signed_minor(rec)
            per_currency[rec["currency"]] += row["effective_minor"]
        rows.append(row)

    currency_lines = []
    for cur in sorted(per_currency):
        minor = per_currency[cur]
        currency_lines.append(dict(
            currency=cur,
            net_minor=minor,
            status="balanced" if minor == 0 else "unbalanced"))
    return dict(
        txn=txn,
        cross_currency=len(per_currency) > 1,
        totals=currency_lines,
        entries=rows,
        lineage=[x for x in report["lineage"] if x["txn"] == txn])


# --- CLI -------------------------------------------------------------------
def cmd_apply(opt):
    root = Path(opt.root)
    root.mkdir(parents=True, exist_ok=True)
    raw = opt.source.read_bytes()
    try:
        batch = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        raise UserError("cannot parse source JSON: " + str(err))

    store = load_store(root)
    prev_version = len(store["batches"])  # each distinct batch = one version
    prev_moves = load_json(
        root / "reports" / ("moves-v" + str(prev_version) + ".json"), {})

    changed, fingerprint = ingest_batch(store, batch, raw)

    if not changed and fingerprint in store["batches"]:
        # Duplicate delivery: no new version, the published report is untouched.
        pub = load_json(root / "reports" / "latest.json", {
            "version": 0, "balances": [], "daily": [], "monthly": [],
            "transactions": [], "unbalanced": [],
            "cross_currency_transactions": [], "exceptions": [],
            "lineage": [], "corrections": []})
        print(dump_json(pub), end="")
        return

    report = compute_report(store)
    version = prev_version + 1
    corrections = build_corrections(prev_moves, report["_moves"],
                                    store["entries"])
    if fingerprint not in store["batches"]:
        store["batches"].append(fingerprint)
    pub = publish(root, store, report, version, corrections,
                  opt.crash_before_publish)
    print(dump_json(pub), end="")


def cmd_inspect(opt):
    root = Path(opt.root)
    if opt.version is not None:
        path = root / "reports" / ("v" + str(opt.version) + ".json")
        if not path.exists():
            raise UserError("version " + str(opt.version) + " not found")
        print(path.read_text(encoding="utf-8"), end="")
        return
    latest = root / "reports" / "latest.json"
    if latest.exists():
        print(latest.read_text(encoding="utf-8"), end="")
    else:
        print(dump_json({
            "version": 0, "balances": [], "daily": [], "monthly": [],
            "transactions": [], "unbalanced": [],
            "cross_currency_transactions": [], "exceptions": [],
            "lineage": [], "corrections": []}), end="")


def cmd_trace(opt):
    store = load_store(Path(opt.root))
    print(dump_json(trace_transaction(store, opt.txn)), end="")


def main():
    p = argparse.ArgumentParser(description="Exact integer reconciliation")
    p.add_argument("root", type=Path)
    sub = p.add_subparsers(dest="op", required=True)

    a = sub.add_parser("apply", help="ingest one batch and republish")
    a.add_argument("source", type=Path)
    a.add_argument("--crash-before-publish", action="store_true")
    a.set_defaults(func=cmd_apply)

    q = sub.add_parser("inspect", help="show a published report")
    q.add_argument("--version", type=int)
    q.set_defaults(func=cmd_inspect)

    t = sub.add_parser("trace", help="trace one transaction to raw rows")
    t.add_argument("txn")
    t.set_defaults(func=cmd_trace)

    opt = p.parse_args()
    try:
        opt.func(opt)
    except UserError as err:
        sys.stderr.write(str(err) + "\n")
        sys.exit(2)
    except (OSError, KeyError, ValueError) as err:
        sys.stderr.write(str(err) + "\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
