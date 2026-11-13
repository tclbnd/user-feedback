"""Acceptance tests for the crash-safe resumable collector.

The harness drives collector.py exactly like an operator (subprocess CLI),
keeps an *independent* snapshot walker that reads the fixture spec directly,
and asserts key-by-key equality of rows and exact equality of delete lists.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC_DIR = ROOT / "specs"
STORE_ROOT = ROOT / "test-stores"


def row(rid, sort, value):
    return dict(id=rid, sort=sort, value=value)


def page(items, etag, nxt, faults=None):
    out = dict(items=items, etag=etag, next=nxt)
    if faults:
        out["faults"] = faults
    return out


def write_spec(name, spec):
    SPEC_DIR.mkdir(exist_ok=True)
    path = SPEC_DIR / (name + ".json")
    path.write_text(json.dumps(spec, indent=1))
    return path


def build_specs():
    # --- token v1: six rows over three normal pages ---------------------
    v1 = dict(
        initial=page([row("a", 1, "a1"), row("b", 2, "b1")], "v1-p1", "p2"),
        p2=page([row("c", 3, "c1"), row("d", 4, "d1")], "v1-p2", "p3"),
        p3=page([row("e", 5, "e1"), row("f", 6, "f1")], "v1-p3", None),
    )

    # --- token v2: after page 2 the source mutated ordering / set --------
    # Mutations vs v1:
    #   b: sort 2 -> 0 (sort key changed, moves ahead of page 2 boundary)
    #   d: value d1 -> d2 (update)
    #   f: deleted
    #   g: inserted (sort 7)
    # Plus: one empty intermediate page, one duplicated page (same etag at a
    # different cursor) and one cross-page duplicate row, plus one 429.
    v2 = dict(
        initial=page([row("b", 0, "b1"), row("a", 1, "a1")], "v2-p1", "p2"),
        p2=page([row("c", 3, "c1"), row("d", 4, "d2")], "v2-p2", "p3",
                faults=[429]),
        p3=page([], "v2-p3", "p4"),  # empty intermediate page
        p4=page([row("c", 3, "c1"), row("d", 4, "d2")], "v2-p2", "p5"),
        p5=page([row("c", 3, "c1"), row("e", 5, "e1"),
                 row("g", 7, "g1")], "v2-p5", None),
    )
    write_spec("spec_v1", dict(tokens=["v1"], pages=dict(v1=v1)))
    write_spec("spec_v2", dict(tokens=["v2"], pages=dict(v2=v2)))

    # --- crash spec: plain token v2 content, no faults -------------------
    crash_v2 = dict(
        initial=page([row("b", 0, "b1"), row("a", 1, "a1")], "c-p1", "p2"),
        p2=page([row("c", 3, "c1"), row("d", 4, "d2")], "c-p2", "p3"),
        p3=page([row("e", 5, "e1"), row("g", 7, "g1")], "c-p3", None),
    )
    write_spec("spec_crash",
               dict(tokens=["v2c"], pages=dict(v2c=crash_v2)))

    # --- expiry spec: old token v2x dies with 410, replacement v3x -------
    old = dict(
        initial=page([row("a", 1, "stale")], "x-p1", "p2", faults=[410]),
        p2=page([row("z", 9, "stale")], "x-p2", None),
    )
    new = dict(
        initial=page([row("a", 1, "fresh"), row("n", 2, "new")],
                     "n-p1", "p2"),
        p2=page([row("q", 3, "q1")], "n-p2", None),
    )
    write_spec("spec_expiry",
               dict(tokens=["v2x", "v3x"], pages=dict(v2x=old, v3x=new)))

    # --- ring spec: page 2 points back at initial -------------------------
    ring = dict(
        initial=page([row("a", 1, "a1")], "r-p1", "loop"),
        loop=page([row("b", 2, "b1")], "r-p2", "initial"),
    )
    write_spec("spec_ring", dict(tokens=["ring"], pages=dict(ring=ring)))

    # --- missing cursor spec: next points at an unknown key ---------------
    missing = dict(initial=page([row("a", 1, "a1")], "m-p1", "ghost"))
    write_spec("spec_missing",
               dict(tokens=["miss"], pages=dict(miss=missing)))

    # --- bad ordering spec: (sort,id) decreases inside a page -------------
    bad = dict(initial=page([row("a", 2, "a1"), row("b", 1, "b1")],
                            "bad-p1", None))
    write_spec("spec_badsort", dict(tokens=["bad"], pages=dict(bad=bad)))


# ----------------------------------------------------- independent reference
def independent_snapshot(spec, token):
    """Walk the fixture pages directly, applying the same dedup contract."""
    rows = {}
    cursor = None
    seen = set()
    pages = spec["pages"][token]
    while True:
        key = cursor if cursor is not None else "initial"
        assert key not in seen, "reference walker hit a cursor ring"
        seen.add(key)
        p = pages[key]
        for r in p["items"]:
            rows.setdefault(r["id"], r)
        if p["next"] is None:
            break
        cursor = p["next"]
    return sorted(rows.values(), key=lambda r: r["id"])


def expected_diff(prev_rows, cur_rows):
    old = dict((r["id"], r) for r in prev_rows)
    new = dict((r["id"], r) for r in cur_rows)
    return dict(
        insert=sorted(set(new) - set(old)),
        delete=sorted(set(old) - set(new)),
        update=sorted(k for k in set(new) & set(old) if new[k] != old[k]),
    )


# --------------------------------------------------------------------- driver
def run_cli(store, *args, expect=0):
    proc = subprocess.run(
        [sys.executable, "collector.py", str(store)] + list(args),
        cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != expect:
        raise AssertionError(
            "cli %s expected rc=%d got %d\nstdout=%s\nstderr=%s"
            % (args, expect, proc.returncode, proc.stdout, proc.stderr))
    out = json.loads(proc.stdout) if proc.stdout.strip() else dict()
    return out, proc


def fresh_store(name):
    d = STORE_ROOT / name
    if d.exists():
        shutil.rmtree(d)
    return d


def inspect(store, version=None):
    args = ["inspect"]
    if version is not None:
        args += ["--version", str(version)]
    out, _ = run_cli(store, *args)
    return out


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


# --------------------------------------------------------------------- tests
def test_acceptance_one_mutations():
    print("AC1: mutations after page 2 + empty/duplicate pages, exact diff")
    store = fresh_store("ac1")
    spec1 = json.loads((SPEC_DIR / "spec_v1.json").read_text())
    spec2 = json.loads((SPEC_DIR / "spec_v2.json").read_text())
    ref1 = independent_snapshot(spec1, "v1")
    ref2 = independent_snapshot(spec2, "v2")

    out1, _ = run_cli(store, "collect", SPEC_DIR / "spec_v1.json")
    check(out1["version"] == 1 and out1["rows"] == ref1,
          "v1 published equal to independent snapshot key-by-key")

    out2, _ = run_cli(store, "collect", SPEC_DIR / "spec_v2.json")
    check(out2["version"] == 2, "second full snapshot is version 2")
    check(out2["rows"] == ref2,
          "v2 rows equal independent snapshot (sort move/insert/delete)")
    check(out2["diff"] == expected_diff(ref1, ref2),
          "diff incl. delete list exactly equals reference diff")
    check(out2["diff"]["delete"] == ["f"], "delete list exactly [f]")
    check(out2["diff"]["update"] == ["b", "d"],
          "update list exactly [b, d] (b's sort key changed, d's value)")
    check(out2["diff"]["insert"] == ["g"], "insert list exactly [g]")
    diag = out2["diagnostics"]
    check(diag["duplicate_pages"] == 1, "duplicated page counted once")
    check(diag["duplicate_rows"] == 3,
          "duplicate rows counted: c,d on duplicated page + c on p5 = 3")
    check([e["reason"] for e in diag["retries"]] == ["rate_limited"],
          "429 retry reason recorded")
    check(inspect(store)["rows"] == out2["rows"],
          "public view equals published v2")
    h1 = inspect(store, 1)
    check(h1["rows"] == ref1 and h1["diff"]["delete"] == [],
          "historical version 1 fully reviewable")
    again, _ = run_cli(store, "collect", SPEC_DIR / "spec_v2.json")
    check(again.get("already_published") is True and again["version"] == 2,
          "re-collecting same token does not add a version")


def _crash_case(store_name, crash, budget_first, expect_304):
    store = fresh_store(store_name)
    spec = SPEC_DIR / "spec_crash.json"
    spec_obj = json.loads(spec.read_text())
    ref = independent_snapshot(spec_obj, "v2c")

    run_cli(store, "collect", SPEC_DIR / "spec_v1.json")
    before = inspect(store)
    check(before["version"] == 1, "baseline v1 published before crash")

    _, crashed = run_cli(store, "collect", spec,
                         "--crash", crash,
                         "--max-requests", str(budget_first), expect=97)
    check(crashed.returncode == 97, "process exited 97 on " + crash)
    during = inspect(store)
    check(during == before, "readers still see the previous complete snapshot")
    check(not (store / "published.json.tmp").exists(),
          "no torn public file left behind")
    staged = list(store.glob("gen-*/page-*.json"))
    check(len(staged) >= 1, "at least one page payload staged durably")

    recovered, _ = run_cli(store, "collect", spec)
    check(recovered["version"] == 2 and recovered["rows"] == ref,
          "recovery publishes complete v2 matching independent snapshot")
    check(recovered["diff"] == expected_diff(before["rows"], ref),
          "recovered diff (incl. deletes) exactly matches reference")
    diag = recovered["diagnostics"]
    if expect_304:
        if crash == "before-publish":
            # All pages were already durable with the terminal cursor;
            # recovery validates the staged chain and publishes without
            # re-fetching anything.
            check(diag["etag_304_reuses"] == 0
                  and diag["requests_this_run"] == 0,
                  "before-publish recovery re-fetches nothing and republishes")
        else:
            check(diag["etag_304_reuses"] >= 1,
                  "recovery reused staged payload via ETag 304")
    else:
        check(diag["etag_304_reuses"] == 0,
              "no 304 when nothing was staged before the crash")
    check(inspect(store, 1)["version"] == 1,
          "history v1 remains intact after recovery")


def test_acceptance_two_crashes():
    print("AC2a: crash after-stage, payload preserved and reused via 304")
    _crash_case("ac2a", "after-stage", 5, expect_304=True)
    print("AC2b: crash before-cursor, cursor never publicly advanced")
    _crash_case("ac2b", "before-cursor", 5, expect_304=True)
    print("AC2c: crash before-publish, full snapshot never visible early")
    _crash_case("ac2c", "before-publish", 50, expect_304=True)


def test_acceptance_two_expiry():
    print("AC2d: injected token expiry, old staging discarded, new gen wins")
    store = fresh_store("ac2d")
    run_cli(store, "collect", SPEC_DIR / "spec_v1.json")
    before = inspect(store)

    out, _ = run_cli(store, "collect", SPEC_DIR / "spec_expiry.json")
    check(out["token"] == "v3x" and out["version"] == 2,
          "only the replacement token's full content is published")
    spec_obj = json.loads((SPEC_DIR / "spec_expiry.json").read_text())
    ref = independent_snapshot(spec_obj, "v3x")
    check(out["rows"] == ref, "v3x rows equal independent snapshot")
    check(out["diff"] == expected_diff(before["rows"], ref),
          "diff to previous complete snapshot is exact (deletes included)")
    # The 410 event belongs to the abandoned generation, which is archived
    # rather than resumed; verify the reason is recorded durably there.
    archived_reasons = []
    for gj in (store / "archive").glob("gen-*/gen.json"):
        g = json.loads(gj.read_text())
        archived_reasons += [e["reason"] for e in g["retry_events"]
                             if e["code"] == 410]
    check(archived_reasons == ["token_expired"],
          "410 token_expired reason recorded in archived generation")
    leaked = [r for r in out["rows"] if r["value"] == "stale"]
    check(leaked == [], "no rows from the expired token leaked")
    archived = list((store / "archive").glob("gen-*"))
    check(len(archived) >= 2, "expired generation archived, not resumed")
    check(inspect(store, 1)["token"] == "v1",
          "previous complete snapshot stays readable throughout")


def test_budget_resume():
    print("AC3: budget exhaustion keeps public view; later run completes")
    store = fresh_store("ac3")
    run_cli(store, "collect", SPEC_DIR / "spec_v1.json")
    before = inspect(store)
    spec = SPEC_DIR / "spec_crash.json"
    spec_obj = json.loads(spec.read_text())
    ref = independent_snapshot(spec_obj, "v2c")

    # Two requests: page 1 is fully durable, page 2 is staged before the
    # budget stops the process, so recovery must conditional-read it (304).
    out, _ = run_cli(store, "collect", spec, "--max-requests", "2")
    check(out.get("incomplete") is True, "budget exhaustion reported incomplete")
    check(inspect(store) == before, "public view unchanged while staging")
    out2, _ = run_cli(store, "collect", spec, "--max-requests", "1")
    # One more page is fetched and durably saved; budget may stop the run
    # either before the final page (incomplete) or exactly at completion.
    check(out2.get("incomplete") is True or out2.get("version") == 2,
          "second bounded run advances the durable progress")
    done, _ = run_cli(store, "collect", spec)
    check(done["rows"] == ref and done["version"] == 2,
          "eventual publication equals independent full snapshot")
    check(done["diagnostics"]["requests_this_run"] <= 2,
          "final bounded resume only fetched the pages not yet durable")


def test_contract_violations():
    print("AC4: cursor ring / 404 / bad ordering fail with exit 2, no publish")
    for name in ("spec_ring", "spec_missing", "spec_badsort"):
        store = fresh_store("contract-" + name)
        _, proc = run_cli(store, "collect", SPEC_DIR / (name + ".json"),
                          expect=2)
        check(proc.returncode == 2, name + " rejected as contract error")
        check(not (store / "published.json").exists(),
              name + " published nothing")


def main():
    if STORE_ROOT.exists():
        shutil.rmtree(STORE_ROOT)
    build_specs()
    test_acceptance_one_mutations()
    test_acceptance_two_crashes()
    test_acceptance_two_expiry()
    test_budget_resume()
    test_contract_violations()
    print("\nALL ACCEPTANCE TESTS PASSED")


if __name__ == "__main__":
    main()
