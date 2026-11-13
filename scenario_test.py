"""End-to-end scenario tests for the snapshot paging collector.

The reference snapshot is computed independently by walking the fixture graph
straight from the spec, then compared key-by-key (including the delete list)
with what the durable collector publishes.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("test-tmp")
COLLECTOR = [sys.executable, "collector.py"]
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  " + detail if detail else ""))


def run(*args):
    proc = subprocess.run(COLLECTOR + list(args), capture_output=True,
                          text=True)
    out = None
    if proc.stdout.strip().startswith("{"):
        out = json.loads(proc.stdout)
    return proc.returncode, out, proc.stderr


def write_spec(name, spec):
    path = ROOT / (name + ".json")
    path.write_text(json.dumps(spec, sort_keys=True), encoding="utf-8")
    return path


def row(rid, sort, value):
    return {"id": rid, "sort": sort, "value": value}


def reference_snapshot(spec, token):
    """Independent walk of the source graph: dedupe by id, sort by id."""
    pages = spec["pages"][token]
    rows = {}
    cursor = None
    while True:
        key = "initial" if cursor is None else cursor
        page = pages[key]
        for item in page["items"]:
            assert item["id"] not in rows or rows[item["id"]] == item
            rows[item["id"]] = item
        if page["next"] is None:
            break
        cursor = page["next"]
    return sorted(rows.values(), key=lambda r: r["id"])


def ids(rows):
    return sorted(r["id"] for r in rows)


def fresh_store(name):
    path = ROOT / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


# ---------------------------------------------------------------- fixtures --
spec_a = {
    "tokens": ["tok-a"],
    "pages": {"tok-a": {
        "initial": {"etag": "a1", "next": "p2",
                    "items": [row("r1", 10, "a1"), row("r2", 20, "a2"),
                              row("r3", 30, "a3"), row("r4", 40, "a4")]},
        "p2": {"etag": "a2", "next": "p3",
               "items": [row("r5", 50, "a5"), row("r6", 60, "a6"),
                         row("r7", 65, "a7"), row("r8", 70, "a8")]},
        "p3": {"etag": "a3", "next": "p4",
               "items": [row("r9", 72, "a9"), row("r3", 30, "a3")]},
        "p4": {"etag": "a4", "next": None,
               "items": [row("r10", 80, "a10")]}}}}

# Mutations versus A: r4 sort key moves (40 -> 75) and changes value,
# r7 value changes, r6/r9 deleted, r11/r12 inserted; plus an empty middle
# page, a repeated page and a repeated row across pages.
spec_b = {
    "tokens": ["tok-b"],
    "pages": {"tok-b": {
        "initial": {"etag": "b1", "next": "p2",
                    "items": [row("r1", 10, "a1"), row("r2", 20, "a2"),
                              row("r11", 25, "b11"), row("r3", 30, "a3")]},
        "p2": {"etag": "b2", "next": "p3", "items": [row("r5", 50, "a5")]},
        "p3": {"etag": "b3", "next": "p4", "items": []},
        "p4": {"etag": "b4", "next": "p5",
               "items": [row("r7", 60, "b7"), row("r4", 75, "b4")]},
        "p5": {"etag": "b5", "next": "p6",
               "items": [row("r8", 70, "a8"), row("r10", 80, "a10")]},
        "p6": {"etag": "b6", "next": "p7",
               "items": [row("r12", 85, "b12")]},
        "p7": {"etag": "b2", "next": "p8", "items": [row("r5", 50, "a5")]},
        "p8": {"etag": "b8", "next": None,
               "items": [row("r1", 10, "a1")]}}}}

spec_c = {
    "tokens": ["tok-c"],
    "pages": {"tok-c": {
        "initial": {"etag": "c1", "next": "c2",
                    "items": [row("c1", 1, "v"), row("c2", 2, "v")]},
        "c2": {"etag": "c2", "next": "c3",
               "items": [row("c3", 3, "v"), row("c4", 4, "v")]},
        "c3": {"etag": "c3", "next": "c4", "items": [row("c5", 5, "v")]},
        "c4": {"etag": "c4", "next": None,
               "items": [row("c6", 6, "v")]}}}}

spec_429 = {
    "tokens": ["tok-d"],
    "pages": {"tok-d": {
        "initial": {"etag": "d1", "next": "p2",
                    "items": [row("d1", 1, "v")]},
        "p2": {"etag": "d2", "next": "p3", "faults": [429],
               "items": [row("d2", 2, "v")]},
        "p3": {"etag": "d3", "next": None,
               "items": [row("d3", 3, "v")]}}}}

spec_410 = {
    "tokens": ["tok-old", "tok-new"],
    "pages": {
        "tok-old": {
            "initial": {"etag": "o1", "next": "c2",
                        "items": [row("o1", 1, "old")]},
            "c2": {"etag": "o2", "next": "c3",
                   "items": [row("o2", 2, "old")]},
            "c3": {"etag": "o3", "next": None, "faults": [410],
                   "items": [row("o3", 3, "old")]}},
        "tok-new": {
            "initial": {"etag": "n1", "next": "n2",
                        "items": [row("n1", 1, "new"), row("n2", 2, "new")]},
            "n2": {"etag": "n2", "next": None,
                   "items": [row("n3", 3, "new")]}}}}

spec_base = {
    "tokens": ["tok-base"],
    "pages": {"tok-base": {
        "initial": {"etag": "f1", "next": "f2",
                    "items": [row("b1", 1, "base"), row("b2", 2, "base")]},
        "f2": {"etag": "f2", "next": None,
               "items": [row("b3", 3, "base")]}}}}

spec_ring = {
    "tokens": ["tok-g"],
    "pages": {"tok-g": {
        "initial": {"etag": "g1", "next": "p2",
                    "items": [row("g1", 1, "v")]},
        "p2": {"etag": "g2", "next": "p2",
               "items": [row("g2", 2, "v")]}}}}

spec_404 = {"tokens": ["tok-ghost"], "pages": {}}

spec_mixed = {
    "tokens": ["tok-i"],
    "pages": {"tok-i": {
        "initial": {"etag": "i1", "next": "p2",
                    "items": [row("x1", 1, "v1")]},
        "p2": {"etag": "i2", "next": None,
               "items": [row("x1", 1, "v2-different")]}}}}

spec_empty = {
    "tokens": ["tok-j"],
    "pages": {"tok-j": {
        "initial": {"etag": "j1", "next": None, "items": []}}}}


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    a = write_spec("spec-a", spec_a)
    b = write_spec("spec-b", spec_b)
    c = write_spec("spec-c", spec_c)
    d429 = write_spec("spec-429", spec_429)
    e410 = write_spec("spec-410", spec_410)
    fbase = write_spec("spec-base", spec_base)
    gring = write_spec("spec-ring", spec_ring)
    h404 = write_spec("spec-404", spec_404)
    imixed = write_spec("spec-mixed", spec_mixed)
    jempty = write_spec("spec-empty", spec_empty)

    print("== 1. full collection vs independent reference snapshot ==")
    store = fresh_store("s1")
    rc, out, err = run(str(store), "collect", str(a))
    ref_a = reference_snapshot(spec_a, "tok-a")
    check("A publishes version 1", rc == 0 and out["version"] == 1, err)
    check("A rows equal reference key-by-key", out["rows"] == ref_a)
    check("A diff inserts all ids",
          out["diff"] == {"insert": ids(ref_a), "update": [], "delete": []})
    rc, view, _ = run(str(store), "inspect")
    check("inspect returns exactly the four public keys",
          sorted(view.keys()) == ["diff", "rows", "token", "version"])
    rc, out2, _ = run(str(store), "collect", str(a))
    check("same token re-collect is idempotent",
          out2["idempotent"] and out2["version"] == 1)

    print("-- mutate after page 2: moved sort key, inserts, deletes, "
          "empty/duplicate pages --")
    rc, out, err = run(str(store), "collect", str(b))
    ref_b = reference_snapshot(spec_b, "tok-b")
    check("B publishes version 2", rc == 0 and out["version"] == 2, err)
    check("B rows equal independent snapshot (no lost moved/deleted rows)",
          out["rows"] == ref_b)
    check("B delete list is exactly r6,r9",
          out["diff"]["delete"] == ["r6", "r9"])
    check("B insert list is exactly r11,r12",
          out["diff"]["insert"] == ["r11", "r12"])
    check("B update list is exactly r4,r7",
          out["diff"]["update"] == ["r4", "r7"])
    check("stale rows from A do not survive",
          set(ids(out["rows"])).isdisjoint({"r6", "r9"}))
    check("moved row r4 carries new sort/value",
          next(r for r in out["rows"] if r["id"] == "r4")
          == row("r4", 75, "b4"))
    check("duplicate row count recorded",
          out["diagnostics"]["duplicate_rows"] == 2)
    check("duplicate page counted once by etag; empty page not counted",
          out["diagnostics"]["duplicate_pages"] == 1)
    check("source version (token) recorded", out["token"] == "tok-b")

    print("== 2. crash: after-stage / before-cursor ==")
    for point in ("after-stage", "before-cursor"):
        st = fresh_store("crash-" + point)
        rc, out, err = run(str(st), "collect", str(c), "--crash", point)
        check(point + " exits 97", rc == 97, err)
        rc, view, _ = run(str(st), "inspect")
        check(point + " readers still see initial state",
              view["version"] == 0 and view["rows"] == [])
        page_file = st / "staging" / "gen-0001" / "page-initial.json"
        check(point + " first payload is durable", page_file.exists())
        meta = json.loads((st / "staging" / "meta.json").read_text())
        check(point + " consume cursor not advanced",
              meta["cursor"] is None and meta["seen_cursors"] == [])
        rc, out, err = run(str(st), "collect", str(c))
        check(point + " recovery publishes", rc == 0
              and out["status"] == "published", err)
        check(point + " recovered rows equal reference",
              out["rows"] == reference_snapshot(spec_c, "tok-c"))
        check(point + " recovery used a 304 conditional read",
              out["diagnostics"]["retries"]["304"] == 1)
        check(point + " 304 replay is not counted as a duplicate page",
              out["diagnostics"]["duplicate_pages"] == 0)

    print("== 3. crash: before-publish ==")
    st = fresh_store("crash-before-publish")
    rc, _, err = run(str(st), "collect", str(c), "--crash", "before-publish")
    check("before-publish exits 97", rc == 97, err)
    rc, view, _ = run(str(st), "inspect")
    check("old public view stays complete (initial here)",
          view["version"] == 0)
    meta = json.loads((st / "staging" / "meta.json").read_text())
    check("all pages consumed and marked done", meta["done"] is True)
    rc, out, err = run(str(st), "collect", str(c))
    check("resume publishes complete snapshot",
          rc == 0 and out["version"] == 1, err)
    check("published rows equal reference",
          out["rows"] == reference_snapshot(spec_c, "tok-c"))

    print("== 4. token expiry (410): discard staging, new generation ==")
    st = fresh_store("s410")
    rc, out, err = run(str(st), "collect", str(e410))
    ref_new = reference_snapshot(spec_410, "tok-new")
    check("410 run still completes", rc == 0, err)
    check("only the new token is published",
          out["version"] == 1 and out["token"] == "tok-new")
    check("new token rows equal its independent snapshot",
          out["rows"] == ref_new)
    check("410 retry reason recorded",
          out["diagnostics"]["retries"]["410"] == 1)
    counter = json.loads((st / "gen-counter.json").read_text())
    check("a fresh generation was opened for the new token",
          counter["next"] == 3)
    check("old token rows never leak into the new snapshot",
          set(ids(out["rows"])).isdisjoint({"o1", "o2", "o3"}))
    kinds = [ev["kind"] for ev in out["diagnostics"]["events"]]
    check("expiry and discard events recorded",
          "token-expired" in kinds and "generation-discarded" in kinds)
    check("no staging left after publish", not (st / "staging").exists())

    print("-- readers keep previous complete snapshot while recovery runs --")
    st = fresh_store("s410-reader")
    rc, base_out, err = run(str(st), "collect", str(fbase))
    base_version = base_out["version"]
    rc, out, err = run(str(st), "collect", str(e410), "--max-requests", "2")
    check("budget run reports incomplete", rc == 0
          and out["status"] == "incomplete", err)
    rc, view, _ = run(str(st), "inspect")
    check("public view unchanged during interruption",
          view["version"] == base_version and view["token"] == "tok-base")
    rc, out, err = run(str(st), "collect", str(e410))
    check("resume publishes only the new token's full content",
          rc == 0 and out["token"] == "tok-new"
          and out["rows"] == ref_new, err)
    check("new version diff deletes the old ids",
          out["diff"]["delete"] == ["b1", "b2", "b3"])

    print("== 5. rate limiting (429) with retry_after ==")
    st = fresh_store("s429")
    rc, out, err = run(str(st), "collect", str(d429))
    check("429 collection completes", rc == 0 and out["version"] == 1, err)
    check("429 counted with reason",
          out["diagnostics"]["retries"]["429"] == 1)
    retry_ev = [e for e in out["diagnostics"]["events"]
                if e.get("kind") == "retry"]
    check("retry event carries reason and retry_after",
          retry_ev and retry_ev[0]["reason"] == 429
          and "retry_after" in retry_ev[0])
    check("rows still complete after retry",
          ids(out["rows"]) == ["d1", "d2", "d3"])

    print("== 6. budget exhaustion then safe resume ==")
    st = fresh_store("sbudget")
    rc, out, err = run(str(st), "collect", str(c), "--max-requests", "1")
    check("first run incomplete with progress",
          rc == 0 and out["status"] == "incomplete"
          and out["progress"]["pages_consumed"] == 1, err)
    rc, view, _ = run(str(st), "inspect")
    check("nothing published after budget stop", view["version"] == 0)
    rc, out, err = run(str(st), "collect", str(c))
    check("resumed run publishes full reference",
          rc == 0 and out["rows"]
          == reference_snapshot(spec_c, "tok-c"), err)

    print("== 7. cursor loop and 404 contracts ==")
    st = fresh_store("sloop")
    rc, out, err = run(str(st), "collect", str(gring))
    check("cursor loop exits 2", rc == 2 and "cursor loop" in err, err)
    check("loop leaves staging for diagnosis",
          (st / "staging" / "meta.json").exists())
    rc, view, _ = run(str(st), "inspect")
    check("loop never publishes", view["version"] == 0)
    st = fresh_store("s404")
    rc, _, err = run(str(st), "collect", str(h404))
    check("unknown token/cursor exits 2 via 404", rc == 2 and "404" in err)

    print("== 8. mixed versions inside one token are rejected ==")
    st = fresh_store("smixed")
    rc, _, err = run(str(st), "collect", str(imixed))
    check("conflicting same-id content rejected",
          rc == 2 and "mixes versions" in err, err)

    print("== 9. empty snapshot and full historical review ==")
    st = fresh_store("sempty")
    rc, out, err = run(str(st), "collect", str(jempty))
    check("empty token publishes empty rows",
          rc == 0 and out["rows"] == [] and out["version"] == 1, err)
    rc, v1, _ = run(str(store), "inspect", "--version", "1")
    rc, v2, _ = run(str(store), "inspect", "--version", "2")
    check("history v1 fully reviewable", v1["rows"] == ref_a
          and v1["token"] == "tok-a")
    check("history v2 fully reviewable with exact diff",
          v2["rows"] == ref_b and v2["diff"] ==
          {"insert": ["r11", "r12"], "update": ["r4", "r7"],
           "delete": ["r6", "r9"]})
    rc, _, err = run(str(store), "inspect", "--version", "99")
    check("unknown historical version errors", rc == 2 and "unknown" in err)

    print("== 10. stale staging when source swaps token between restarts ==")
    st = fresh_store("sswap")
    run(str(st), "collect", str(c), "--max-requests", "1")
    rc, out, err = run(str(st), "collect", str(a))
    check("old staging discarded and new token fully published",
          rc == 0 and out["token"] == "tok-a"
          and out["rows"] == ref_a, err)
    check("stale-staging event recorded",
          any(e.get("kind") == "stale-staging"
              for e in out["diagnostics"]["events"]))

    print()
    print("TOTAL: %d passed, %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
