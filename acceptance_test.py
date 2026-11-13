"""Acceptance harness for the exact integer reconciliation ledger.

Scenarios covered:
  * cross-month postings and month-end snapshots
  * partial / repeated / late reversals and direction reversal
  * revision supersedence (highest rev per id) and idempotent re-delivery
  * cross-currency transactions that must never offset
  * BAD_AMOUNT / REVERSAL_TARGET / REVERSAL_MISMATCH / REVERSAL_EXCESS
  * shuffled batch and entry arrival -> byte-identical final report
  * crash before publish (exit 97) -> old report intact, resume converges
  * trace from a transaction total down to each raw source row/version

Run: python3 acceptance_test.py
Exits 0 only when every assertion passes.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

SCALE = dict(USD=2, EUR=2, JPY=0)


def jan_entries():
    src = "January"
    return [
        dict(id="r1", rev=1, txn="sale", date="2026-01-31", amount="100.10",
             side="debit", account="cash", currency="USD", source=src, line=1),
        dict(id="r2", rev=1, txn="sale", date="2026-01-31", amount="100.10",
             side="credit", account="sales", currency="USD", source=src, line=2),
        dict(id="r3", rev=1, txn="fees", date="2026-01-15", amount="50.00",
             side="debit", account="fees", currency="USD", source=src, line=3),
        dict(id="r4", rev=1, txn="ghost", date="2026-01-10", amount="9.99",
             side="debit", account="cash", currency="USD", source=src, line=4,
             void=True),
        dict(id="r5", rev=1, txn="badamt", date="2026-01-11", amount="12.345",
             side="debit", account="cash", currency="USD", source=src, line=5),
        dict(id="r6", rev=1, txn="unknownccy", date="2026-01-12", amount="1.00",
             side="debit", account="cash", currency="XYZ", source=src, line=6),
        dict(id="r7", rev=1, txn="jpycash", date="2026-01-20", amount="1000",
             side="debit", account="cash_jpy", currency="JPY", source=src, line=7),
        dict(id="r8", rev=1, txn="fx", date="2026-01-22", amount="100.00",
             side="debit", account="cash", currency="USD", source=src, line=8),
        dict(id="r9", rev=1, txn="fixme", date="2026-01-23", amount="10.00",
             side="debit", account="suspense", currency="USD", source=src, line=9),
        dict(id="r10", rev=1, txn="dup", date="2026-01-05", amount="5.00",
             side="debit", account="cash", currency="USD", source=src, line=10),
        dict(id="r25", rev=1, txn="jpybad", date="2026-01-21", amount="100.5",
             side="debit", account="cash_jpy", currency="JPY", source=src, line=11),
    ]


def feb_entries():
    src = "February"
    return [
        dict(id="r11", rev=1, txn="fees", date="2026-02-03", amount="20.00",
             side="credit", account="fees", currency="USD", source=src, line=1,
             reverses="r3"),
        dict(id="r12", rev=1, txn="fees", date="2026-02-04", amount="20.00",
             side="credit", account="fees", currency="USD", source=src, line=2,
             reverses="r3"),
        dict(id="r13", rev=1, txn="fees", date="2026-02-05", amount="20.00",
             side="credit", account="fees", currency="USD", source=src, line=3,
             reverses="r3"),  # exceeds remaining 10 -> EXCESS
        dict(id="r14", rev=1, txn="orphan", date="2026-02-06", amount="5.00",
             side="credit", account="cash", currency="USD", source=src, line=4,
             reverses="r999"),  # target never arrives -> TARGET
        dict(id="r15", rev=1, txn="fees", date="2026-02-07", amount="7.00",
             side="debit", account="fees", currency="USD", source=src, line=5,
             reverses="r3"),  # same side as target -> MISMATCH
        dict(id="r16", rev=1, txn="fx", date="2026-02-09", amount="90.00",
             side="credit", account="cash_eur", currency="EUR", source=src, line=6),
        dict(id="r10", rev=1, txn="dup", date="2026-01-05", amount="5.00",
             side="debit", account="cash", currency="USD",
             source="January", line=10),  # identical re-delivery of jan row
        dict(id="r17", rev=1, txn="bills", date="2026-02-10", amount="30.00",
             side="debit", account="bills", currency="USD", source=src, line=8),
        dict(id="r18", rev=1, txn="bills", date="2026-02-10", amount="30.00",
             side="credit", account="ap", currency="USD", source=src, line=9),
    ]


def mar_entries():
    src = "March"
    return [
        dict(id="r19", rev=1, txn="fees", date="2026-03-02", amount="10.00",
             side="credit", account="fees", currency="USD", source=src, line=1,
             reverses="r3"),  # late final reversal, exactly the remaining 10
        dict(id="r9", rev=2, txn="fixme", date="2026-01-23", amount="12.00",
             side="debit", account="suspense", currency="USD", source=src, line=2),
        dict(id="r21", rev=1, txn="jpycash", date="2026-03-05", amount="1000",
             side="credit", account="cash_jpy", currency="JPY", source=src, line=3,
             reverses="r7"),
        dict(id="r22", rev=1, txn="fees", date="2026-01-01", amount="1.00",
             side="credit", account="cash", currency="USD", source=src, line=4,
             reverses="r1"),  # predates target -> MISMATCH
        dict(id="r23", rev=1, txn="fees", date="2026-03-10", amount="1.00",
             side="debit", account="fees", currency="USD", source=src, line=5,
             reverses="r13"),  # reversing a reversal -> TARGET
        dict(id="r24", rev=1, txn="fx", date="2026-03-12", amount="100.00",
             side="credit", account="cash", currency="EUR", source=src, line=6,
             reverses="r1"),  # currency mismatch -> MISMATCH
    ]


BATCHES = {"jan.json": jan_entries, "feb.json": feb_entries, "mar.json": mar_entries}

EXPECTED_BALANCES = [
    ("ap", "USD", -3000),
    ("bills", "USD", 3000),
    ("cash", "USD", 20510),
    ("cash_eur", "EUR", -9000),
    ("cash_jpy", "JPY", 0),
    ("fees", "USD", 0),
    ("sales", "USD", -10010),
    ("suspense", "USD", 1200),
]

EXPECTED_UNBALANCED = [
    ("dup", "USD", 500),
    ("fixme", "USD", 1200),
    ("fx", "EUR", -9000),
    ("fx", "USD", 10000),
]

EXPECTED_EXCEPTIONS = [
    ("r13", "REVERSAL_EXCESS"),
    ("r14", "REVERSAL_TARGET"),
    ("r15", "REVERSAL_MISMATCH"),
    ("r22", "REVERSAL_MISMATCH"),
    ("r23", "REVERSAL_TARGET"),
    ("r24", "REVERSAL_MISMATCH"),
    ("r25", "BAD_AMOUNT"),
    ("r5", "BAD_AMOUNT"),
    ("r6", "BAD_AMOUNT"),
]

EXPECTED_MONTHLY = [
    ("2026-01", "cash", "USD", 20510),
    ("2026-01", "cash_jpy", "JPY", 1000),
    ("2026-01", "fees", "USD", 5000),
    ("2026-01", "sales", "USD", -10010),
    ("2026-01", "suspense", "USD", 1200),
    ("2026-02", "ap", "USD", -3000),
    ("2026-02", "bills", "USD", 3000),
    ("2026-02", "cash_eur", "EUR", -9000),
    ("2026-02", "fees", "USD", -4000),
    ("2026-03", "cash_jpy", "JPY", -1000),
    ("2026-03", "fees", "USD", -1000),
]

PASS = 0


def check(label, cond, detail=""):
    global PASS
    if not cond:
        raise AssertionError(label + (" :: " + detail if detail else ""))
    PASS += 1
    print("  ok - " + label)


def write_batch(path, entries, scale=None):
    payload = dict(currency_scale=scale or SCALE, entries=entries)
    path.write_text(json.dumps(payload, ensure_ascii=False))


def run_cli(root, *args, expect=0):
    proc = subprocess.run(
        [PY, str(HERE / "reconcile.py"), str(root), *args],
        capture_output=True, text=True)
    if proc.returncode != expect:
        raise AssertionError(
            "cli %s exited %d (want %d)\nstdout:%s\nstderr:%s"
            % (args, proc.returncode, expect, proc.stdout, proc.stderr))
    return proc


def run_verify(root, *args, expect=0):
    proc = subprocess.run(
        [PY, str(HERE / "verify.py"), str(root), *args],
        capture_output=True, text=True)
    if proc.returncode != expect:
        raise AssertionError("verify exited %d\n%s" % (proc.returncode,
                                                       proc.stdout + proc.stderr))
    return proc


def report(root, version=None):
    args = ["inspect"]
    if version is not None:
        args += ["--version", str(version)]
    out = run_cli(root, *args).stdout
    return json.loads(out)


def apply_all(root, workdir, order=("jan.json", "feb.json", "mar.json"),
              shuffle_entries=False, crash_at=None):
    """crash_at: 1-based batch index to publish with --crash-before-publish."""
    for idx, name in enumerate(order, start=1):
        entries = list(BATCHES[name]())
        if shuffle_entries:
            random.Random(idx * 7919 + len(name)).shuffle(entries)
        path = workdir / (name + ".shuf" if shuffle_entries else name)
        write_batch(path, entries)
        if crash_at == idx:
            run_cli(root, "apply", path, "--crash-before-publish", expect=97)
        else:
            run_cli(root, "apply", path)


def balances_set(rep):
    return sorted((x["account"], x["currency"], x["minor"])
                  for x in rep["balances"])


def report_core(rep):
    """Ledger body whose value must be arrival-order independent.

    corrections are intentionally excluded: they are diffs between adjacent
    published versions, and the version boundaries themselves shift when
    batches arrive in a different order.
    """
    keys = ("version", "balances", "daily", "monthly", "transactions",
            "unbalanced", "cross_currency_transactions", "exceptions", "lineage")
    return json.dumps(dict((k, rep[k]) for k in keys),
                      sort_keys=True, indent=2, ensure_ascii=False)


def main():
    sandbox = Path(tempfile.mkdtemp(prefix="recon-accept-"))
    try:
        print("[1] baseline run with exact expected figures")
        root = sandbox / "base"
        work = sandbox / "batches"
        work.mkdir(parents=True)
        for name, builder in BATCHES.items():
            write_batch(work / name, builder())
        for name in BATCHES:
            run_cli(root, "apply", work / name)

        rep = report(root)
        check("final version is 3", rep["version"] == 3)
        check("exact integer balances", balances_set(rep) == EXPECTED_BALANCES,
              json.dumps(balances_set(rep)))
        check("all minor values are ints",
              all(isinstance(x["minor"], int) for x in rep["balances"]))
        check("unbalanced list",
              sorted((x["txn"], x["currency"], x["minor"])
                     for x in rep["unbalanced"]) == EXPECTED_UNBALANCED)
        check("exception codes and ordering",
              sorted((x["id"], x["code"]) for x in rep["exceptions"])
              == sorted(EXPECTED_EXCEPTIONS))
        check("exceptions sorted by (id, code)",
              [(x["id"], x["code"]) for x in rep["exceptions"]]
              == sorted(EXPECTED_EXCEPTIONS))
        check("every exception locates source/line",
              all(x["source"] and isinstance(x["line"], int) and x["line"] > 0
                  for x in rep["exceptions"]))
        check("monthly snapshots",
              sorted((x["month"], x["account"], x["currency"], x["minor"])
                     for x in rep["monthly"]) == sorted(EXPECTED_MONTHLY))
        check("cross-currency txn flagged, never offset",
              rep["cross_currency_transactions"] == ["fx"])
        check("zero-balance keys retained (cash_jpy JPY, fees USD)",
              any(x["account"] == "cash_jpy" and x["currency"] == "JPY"
                  and x["minor"] == 0 for x in rep["balances"])
              and any(x["account"] == "fees" and x["minor"] == 0
                      for x in rep["balances"]))
        check("lineage excludes void/bad/failed rows",
              sorted(x["id"] for x in rep["lineage"]) ==
              sorted(["r1", "r2", "r3", "r7", "r8", "r9", "r10", "r11", "r12",
                      "r16", "r17", "r18", "r19", "r21"]))
        check("lineage carries effective rev (r9 -> rev 2)",
              [x for x in rep["lineage"] if x["id"] == "r9"][0]["rev"] == 2)

        print("[2] independent integer-ledger verification")
        run_verify(root)
        for v in (1, 2, 3):
            run_verify(root, "--version", str(v))

        print("[3] intermediate snapshots: late reversal lands in its own month")
        v1 = report(root, 1)
        v2 = report(root, 2)
        m1 = dict(((x["month"], x["account"], x["currency"]), x["minor"])
                  for x in v1["monthly"])
        check("v1 fees +5000 before any reversal",
              m1[("2026-01", "fees", "USD")] == 5000)
        check("v1 suspense booked at original 10.00",
              [b for b in v1["balances"] if b["account"] == "suspense"][0]["minor"]
              == 1000)
        m2 = dict(((x["month"], x["account"], x["currency"]), x["minor"])
                  for x in v2["monthly"])
        check("v2 February reversal posts -4000 in Feb only",
              m2[("2026-02", "fees", "USD")] == -4000
              and m2[("2026-01", "fees", "USD")] == 5000)
        check("v3 corrections: r9 revised +200, r19/r21 new",
              dict((c["id"], (c["kind"], c["delta"])) for c in rep["corrections"])
              == {"r9": ("CHANGED", 200), "r19": ("NEW", -1000),
                  "r21": ("NEW", -1000)})
        check("v1 corrections are all NEW rows",
              all(c["kind"] == "NEW" for c in v1["corrections"])
              and len(v1["corrections"]) == 7)

        print("[4] shuffled batch arrival converges to identical bytes")
        baseline_core = report_core(rep)
        baseline_entries = (root / "store" / "entries.json").read_bytes()
        perms = [
            ("jan.json", "feb.json", "mar.json"),
            ("mar.json", "jan.json", "feb.json"),
            ("feb.json", "mar.json", "jan.json"),
            ("mar.json", "feb.json", "jan.json"),
        ]
        for perm in perms:
            r2 = sandbox / ("perm-" + "_".join(perm))
            w2 = sandbox / ("w-" + "_".join(perm))
            w2.mkdir()
            apply_all(r2, w2, order=perm)
            check("ledger body byte-identical for order " + ",".join(perm),
                  report_core(report(r2)) == baseline_core)
            check("entries store byte-identical for order " + ",".join(perm),
                  (r2 / "store" / "entries.json").read_bytes() == baseline_entries)
            check("corrections stay internally consistent for " + ",".join(perm),
                  all(c["minor_after"] - c["minor_before"] == c["delta"]
                      for v in (1, 2, 3)
                      for c in report(r2, v)["corrections"]))
            run_verify(r2)

        print("[5] shuffled entries inside each batch converge too")
        r3 = sandbox / "shuf"
        w3 = sandbox / "wshuf"
        w3.mkdir()
        apply_all(r3, w3, shuffle_entries=True)
        check("shuffled entries produce identical full report",
              (r3 / "reports" / "latest.json").read_bytes()
              == (root / "reports" / "latest.json").read_bytes())
        run_verify(r3)

        print("[6] crash before publish leaves old report intact and resumes")
        rc = sandbox / "crash"
        wc = sandbox / "wcrash"
        wc.mkdir()
        # crash on the very first publication: nothing exists yet
        first = wc / "jan.json"
        write_batch(first, jan_entries())
        run_cli(rc, "apply", first, "--crash-before-publish", expect=97)
        check("no reports published after pre-publish crash",
              not (rc / "reports").exists()
              and not (rc / "store" / "entries.json").exists())
        check("inspect on empty store reports version 0",
              report(rc)["version"] == 0)
        run_cli(rc, "apply", first)  # resume
        check("resumed first apply publishes version 1", report(rc)["version"] == 1)
        # crash at batch 2
        second = wc / "feb.json"
        write_batch(second, feb_entries())
        run_cli(rc, "apply", second, "--crash-before-publish", expect=97)
        stored_batches = json.loads((rc / "store" / "batches.json").read_text())
        check("v1 still readable and no v2 artefact after crash",
              report(rc)["version"] == 1
              and not (rc / "reports" / "v2.json").exists()
              and len(stored_batches) == 1)
        run_cli(rc, "apply", second)  # resume
        check("resumed apply converges to v2 with same bytes as baseline",
              (rc / "reports" / "v2.json").read_bytes()
              == (root / "reports" / "v2.json").read_bytes())
        third = wc / "mar.json"
        write_batch(third, mar_entries())
        run_cli(rc, "apply", third)
        check("final report after crash/resume is byte-identical (same order)",
              (rc / "reports" / "latest.json").read_bytes()
              == (root / "reports" / "latest.json").read_bytes())
        run_verify(rc)

        print("[7] duplicate batches are idempotent (no new version)")
        before = (root / "reports" / "latest.json").read_bytes()
        run_cli(root, "apply", work / "feb.json")
        run_cli(root, "apply", work / "jan.json")
        check("re-applied batches do not bump version or touch report",
              report(root)["version"] == 3
              and (root / "reports" / "latest.json").read_bytes() == before)

        print("[8] same-rev conflicting content is rejected")
        bad = sandbox / "conflict.json"
        conflict = jan_entries()
        conflict[0] = dict(conflict[0])
        conflict[0]["amount"] = "999.99"  # same id r1, same rev, new content
        write_batch(bad, conflict)
        run_cli(root, "apply", bad, expect=2)
        check("rejected conflict leaves version 3 untouched",
              report(root)["version"] == 3)

        print("[9] trace drills totals down to raw rows and effective versions")
        tr_fx = json.loads(run_cli(root, "trace", "fx").stdout)
        check("trace fx totals per currency",
              sorted((x["currency"], x["net_minor"], x["status"])
                     for x in tr_fx["totals"]) ==
              [("EUR", -9000, "unbalanced"), ("USD", 10000, "unbalanced")])
        check("trace fx marks cross currency", tr_fx["cross_currency"] is True)
        r24_row = [e for e in tr_fx["entries"] if e["id"] == "r24"][0]
        check("trace shows failed reversal with reason and raw source/line",
              r24_row["effective"] is False
              and r24_row["reason"] == "REVERSAL_MISMATCH"
              and (r24_row["source"], r24_row["line"]) == ("March", 6))

        tr_fees = json.loads(run_cli(root, "trace", "fees").stdout)
        rows = dict((e["id"], e) for e in tr_fees["entries"])
        check("fees nets to zero across three partial reversals",
              tr_fees["totals"] == [
                  dict(currency="USD", net_minor=0, status="balanced")])
        check("original r3 still queryable with full chain",
              rows["r3"]["effective"] is True and rows["r3"]["effective_minor"] == 5000
              and rows["r19"]["effective_minor"] == -1000
              and rows["r13"]["reason"] == "REVERSAL_EXCESS"
              and rows["r15"]["reason"] == "REVERSAL_MISMATCH")

        tr_fix = json.loads(run_cli(root, "trace", "fixme").stdout)
        r9 = tr_fix["entries"][0]
        check("fixme traces to effective revision 2 (12.00)",
              len(tr_fix["entries"]) == 1 and r9["rev"] == 2
              and r9["effective_minor"] == 1200 and r9["reason"] == "OK")

        tr_ghost = json.loads(run_cli(root, "trace", "ghost").stdout)
        check("voided original row remains queryable but ineffective",
              tr_ghost["entries"][0]["reason"] == "VOIDED"
              and tr_ghost["entries"][0]["effective"] is False
              and (tr_ghost["entries"][0]["source"],
                   tr_ghost["entries"][0]["line"]) == ("January", 4))
        run_cli(root, "trace", "nope", expect=2)

        print("[10] original demo fixture still passes")
        demo = sandbox / "demo-root"
        run_cli(demo, "apply", HERE / "demo.json")
        drep = report(demo)
        check("demo sale crosses months and ends balanced",
              drep["version"] == 1 and drep["unbalanced"] == []
              and drep["cross_currency_transactions"] == [])
        run_verify(demo)

        print("[11] malformed input rejected with exit 2")
        broken = sandbox / "broken.json"
        broken.write_text("{not json")
        run_cli(sandbox / "broken-root", "apply", broken, expect=2)
        bad2 = sandbox / "bad2.json"
        write_batch(bad2, [dict(id="x", rev=1, txn="t", date="2026-13-40",
                                amount="1.00", side="debit", account="a",
                                currency="USD", source="s", line=1)])
        run_cli(sandbox / "bad2-root", "apply", bad2, expect=2)
        run_cli(root, "inspect", "--version", "99", expect=2)

        print("\nALL %d CHECKS PASSED" % PASS)
        return 0
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
