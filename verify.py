"""Independent integer-ledger verifier.

Recomputes the whole ledger from the raw entry store using a deliberately
separate implementation (plain dicts, explicit loops, no imports from
reconcile.py), then asserts the published report matches and checks internal
invariants. Exits 0 on success, 1 with a printed mismatch list otherwise.

Usage: python3 verify.py ROOT [--version N]
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

CODES = ("BAD_AMOUNT", "REVERSAL_TARGET", "REVERSAL_MISMATCH",
         "REVERSAL_EXCESS")


def read_json(path, default):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def parse_minor(text, scale):
    """Independent decimal parser: integer string arithmetic only."""
    if not isinstance(text, str):
        return None
    parts = text.split(".")
    if len(parts) > 2 or any(not x.isdigit() for x in parts) or not parts[0]:
        return None
    whole = parts[0]
    frac = parts[1] if len(parts) == 2 else ""
    if len(frac) > scale:
        return None
    frac_padded = (frac + "0" * scale)[:scale]
    return int(whole) * (10 ** scale) + (int(frac_padded) if frac_padded else 0)


def independent_ledger(store):
    """Mirror the reconciliation rules without importing reconcile.py."""
    scales = store["currency_scale"]
    raw = store["entries"]

    # Highest rev per id; re-validate determinism of the stored content.
    effective = {}
    for eid, rec in raw.items():
        if eid not in effective or rec["rev"] > effective[eid]["rev"]:
            effective[eid] = rec

    problems = []
    live = {}
    txn_ccy = defaultdict(set)
    for eid in sorted(effective):
        rec = effective[eid]
        if rec["currency"] not in scales:
            problems.append((eid, "BAD_AMOUNT"))
            continue
        minor = parse_minor(rec["amount"], scales[rec["currency"]])
        if minor is None or minor != rec["minor"]:
            problems.append((eid, "BAD_AMOUNT"))
            continue
        if rec["void"]:
            continue
        live[eid] = rec
        txn_ccy[rec["txn"]].add(rec["currency"])

    # Reversals, (date, id) order, remaining-amount checks.
    used = defaultdict(int)
    good_reversals = set()
    for eid in sorted(live, key=lambda k: (live[k]["date"], k)):
        rec = live[eid]
        tgt_id = rec["reverses"]
        if tgt_id is None:
            continue
        tgt = live.get(tgt_id)
        if tgt is None or tgt["reverses"] is not None:
            problems.append((eid, "REVERSAL_TARGET"))
        elif (rec["currency"] != tgt["currency"]
              or rec["account"] != tgt["account"]
              or rec["side"] == tgt["side"]
              or rec["date"] < tgt["date"]):
            problems.append((eid, "REVERSAL_MISMATCH"))
        elif tgt_id == eid or used[tgt_id] + rec["minor"] > tgt["minor"]:
            problems.append((eid, "REVERSAL_EXCESS"))
        else:
            used[tgt_id] += rec["minor"]
            good_reversals.add(eid)

    def signed(rec):
        # A valid reversal is already posted on the opposite side; its
        # debit/credit sign therefore cancels the target directly.
        return rec["minor"] if rec["side"] == "debit" else -rec["minor"]

    bal = defaultdict(int)
    day = defaultdict(int)
    mon = defaultdict(int)
    tnet = defaultdict(int)
    moves = {}
    for eid in sorted(live):
        rec = live[eid]
        if rec["reverses"] is not None and eid not in good_reversals:
            continue
        mv = signed(rec)
        moves[eid] = mv
        bal[(rec["account"], rec["currency"])] += mv
        day[(rec["date"], rec["account"], rec["currency"])] += mv
        mon[(rec["date"][:7], rec["account"], rec["currency"])] += mv
        tnet[(rec["txn"], rec["currency"])] += mv

    expected_ex = sorted(problems)
    return dict(
        balances=sorted((a, c, v) for (a, c), v in bal.items()),
        daily=sorted((d, a, c, v) for (d, a, c), v in day.items()),
        monthly=sorted((m, a, c, v) for (m, a, c), v in mon.items()),
        txn_net=sorted((t, c, v) for (t, c), v in tnet.items()),
        exceptions=expected_ex,
        moves=moves,
        txn_currencies=dict((t, sorted(s)) for t, s in txn_ccy.items()))


def verify(root, version):
    root = Path(root)
    if version is not None:
        snap_path = root / "reports" / ("store-v" + str(version) + ".json")
        if not snap_path.exists():
            return ["missing store snapshot: " + str(snap_path)]
        snap = json.loads(snap_path.read_text())
        store = snap["entries"]
        scales = snap["currency_scale"]
        batches = snap["batches"]
    else:
        store = read_json(root / "store" / "entries.json", {})
        scales = read_json(root / "store" / "currency_scale.json", {})
        batches = read_json(root / "store" / "batches.json", [])
    if not store:
        return ["store is empty; nothing to verify"]
    ledger = independent_ledger(dict(entries=store, currency_scale=scales))

    report_path = (root / "reports" /
                   ("latest.json" if version is None else "v" + str(version) + ".json"))
    if not report_path.exists():
        return ["missing report: " + str(report_path)]
    report = json.loads(report_path.read_text())
    checked_version = report["version"]

    failures = []

    got_bal = sorted((x["account"], x["currency"], x["minor"])
                     for x in report["balances"])
    if got_bal != ledger["balances"]:
        failures.append("balances mismatch")

    got_day = sorted((x["date"], x["account"], x["currency"], x["minor"])
                     for x in report["daily"])
    if got_day != ledger["daily"]:
        failures.append("daily snapshot mismatch")

    got_mon = sorted((x["month"], x["account"], x["currency"], x["minor"])
                     for x in report["monthly"])
    if got_mon != ledger["monthly"]:
        failures.append("monthly snapshot mismatch")

    got_unb = sorted((x["txn"], x["currency"], x["minor"])
                     for x in report["unbalanced"])
    exp_unb = sorted(x for x in ledger["txn_net"] if x[2] != 0)
    if got_unb != exp_unb:
        failures.append("unbalanced list mismatch")

    got_ex = sorted((x["id"], x["code"]) for x in report["exceptions"])
    if got_ex != ledger["exceptions"]:
        failures.append("exceptions mismatch: got=%s exp=%s"
                        % (got_ex, ledger["exceptions"]))
    for ex in report["exceptions"]:
        if ex["code"] not in CODES:
            failures.append("unknown exception code: " + ex["code"])
        src = store.get(ex["id"])
        if src and (src["source"] != ex["source"] or src["line"] != ex["line"]):
            failures.append("exception source/line not locatable: " + ex["id"])

    got_cross = sorted(report["cross_currency_transactions"])
    exp_cross = sorted(t for t, cs in ledger["txn_currencies"].items()
                       if len(cs) > 1)
    if got_cross != exp_cross:
        failures.append("cross-currency list mismatch")

    # Invariants.
    # 1. Balances are exactly the sum of the monthly snapshots.
    roll = defaultdict(int)
    for m, a, c, v in ledger["monthly"]:
        roll[(a, c)] += v
    if sorted(roll.items()) != sorted(((a, c), v)
                                      for (a, c, v) in ledger["balances"]):
        failures.append("monthly snapshots do not roll up to balances")
    roll2 = defaultdict(int)
    for d, a, c, v in ledger["daily"]:
        roll2[(a, c)] += v
    if sorted(roll2.items()) != sorted(((a, c), v)
                                       for (a, c, v) in ledger["balances"]):
        failures.append("daily snapshots do not roll up to balances")

    # 2. No currency ever offsets another inside a transaction.
    for t, cs in ledger["txn_currencies"].items():
        if len(cs) > 1 and t not in report["cross_currency_transactions"]:
            failures.append("cross-currency txn not flagged: " + t)

    # 3. Lineage points only at effective rows and totals trace to them.
    lineage_ids = set(x["id"] for x in report["lineage"])
    if lineage_ids != set(ledger["moves"]):
        failures.append("lineage does not match effective ledger rows")
    tnet_check = defaultdict(int)
    for eid in lineage_ids:
        rec = store[eid]
        tnet_check[(rec["txn"], rec["currency"])] += ledger["moves"][eid]
    if sorted(tnet_check.items()) != [((t, c), v) for (t, c, v)
                                      in ledger["txn_net"]]:
        failures.append("lineage rows do not reproduce per-txn totals")

    # 4. Version bookkeeping and immutable history.
    if report["version"] != len(batches):
        failures.append("report version != number of distinct batches")
    if version is None:
        latest = json.loads((root / "reports" / "latest.json").read_text())
        if latest["version"] != report["version"]:
            failures.append("latest pointer version mismatch")
    for n in range(1, checked_version + 1):
        vp = root / "reports" / ("v" + str(n) + ".json")
        if not vp.exists():
            failures.append("missing immutable report v" + str(n))
            continue
        old = json.loads(vp.read_text())
        if old["version"] != n:
            failures.append("immutable report v" + str(n) + " has wrong version")
        mp = root / "reports" / ("moves-v" + str(n) + ".json")
        if not mp.exists():
            failures.append("missing movement snapshot v" + str(n))
        sp = root / "reports" / ("store-v" + str(n) + ".json")
        if not sp.exists():
            failures.append("missing store snapshot v" + str(n))

    # 5. Corrections reproduce the movement delta between adjacent versions.
    for n in range(1, checked_version + 1):
        rep_n = json.loads((root / "reports" / ("v" + str(n) + ".json")).read_text())
        prev = read_json(root / "reports" / ("moves-v" + str(n - 1) + ".json"), {})
        cur = read_json(root / "reports" / ("moves-v" + str(n) + ".json"), {})
        delta_sum = sum(x["delta"] for x in rep_n["corrections"])
        if sum(cur.values()) - sum(prev.values()) != delta_sum:
            failures.append("correction deltas inconsistent at v" + str(n))

    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--version", type=int)
    opt = ap.parse_args()
    failures = verify(opt.root, opt.version)
    if failures:
        for f in failures:
            print("FAIL: " + f)
        sys.exit(1)
    print("OK: independent integer ledger matches published report")
    sys.exit(0)


if __name__ == "__main__":
    main()
