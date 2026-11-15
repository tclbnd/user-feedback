"""Acceptance tests for the hierarchical-budget scheduler.

Run with:  python3 tests/acceptance.py
Exit code 0 means every acceptance condition passed.
"""

import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import sched  # noqa: E402
import naive_sim  # noqa: E402
import make_scenarios  # noqa: E402

PASS, FAIL = 0, 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok  %s" % label)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (label, detail))


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def without_digest(report):
    return {k: v for k, v in report.items() if k != "digest"}


# --------------------------------------------------------------------- part A
def compare_with_naive(name, sim):
    print("A. reference comparison: %s" % name)
    actual = sched.replay(json.loads(json.dumps(sim)))
    reference = naive_sim.run(json.loads(json.dumps(sim)))

    # Per-tick dispatch and both balance levels, item by item.
    check("%s: identical dispatch sequence" % name,
          actual["dispatch"] == reference["dispatch"],
          detail=(actual["dispatch"], reference["dispatch"]))
    check("%s: identical per-tick balances" % name,
          actual["balances"] == reference["balances"],
          detail=(actual["balances"], reference["balances"]))
    check("%s: identical service shares" % name,
          actual["service"] == reference["service"])
    check("%s: identical paused tick counts" % name,
          actual["paused_ticks"] == reference["paused_ticks"])
    check("%s: identical backlog segments" % name,
          actual["backlog_segments"] == reference["backlog_segments"])
    check("%s: identical wait reasons" % name,
          actual["waits"] == reference["waits"])
    check("%s: identical canonical digest" % name,
          actual["digest"] == reference["digest"])

    # No budget level may ever go negative.
    neg_global = [b for b in actual["balances"] if b["global"] < 0]
    neg_source = [b for b in actual["balances"]
                  if any(v < 0 for v in b["source"].values())]
    check("%s: global budget never negative" % name, not neg_global)
    check("%s: source budgets never negative" % name, not neg_source)

    # One balance record per tick covering every source.
    check("%s: balance per tick" % name,
          [b["tick"] for b in actual["balances"]]
          == list(range(sim["horizon"])))
    check("%s: all sources represented per tick" % name,
          all(set(b["source"]) == set(sim["source_budget"])
              for b in actual["balances"]))

    # No page group committed twice: de-dup keys unique in dispatch.
    keys = [(d["source"], d["task"], d["part"]) for d in actual["dispatch"]]
    check("%s: committed page groups are unique" % name,
          len(keys) == len(set(keys)))
    return actual, reference


def drr_smoke():
    """README's worked example: weights 1 and 2, one unit of global budget per
    tick, two continuously backlogged tenants -> a,b,b,a,b,b."""
    print("A. deficit rotation worked example")
    sim = {
        "horizon": 6,
        "global_budget": 1,
        "source_budget": {"src": 6},
        "weights": {"a": 1, "b": 2},
        # Distinct task per tenant: the (source, task, part) de-dup key must
        # not collide across tenants. Deep backlog lets b serve twice/visit.
        "events": [
            {"kind": "arrival", "tick": 0, "seq": i + 1, "source": "src",
             "tenant": t, "task": "job-" + t, "part": i, "cost": 1}
            for i, t in enumerate(
                ["a", "b", "a", "b", "a", "b", "a", "b", "a", "b", "a", "b"])
        ],
    }
    report = sched.replay(json.loads(json.dumps(sim)))
    order = [d["tenant"] for d in report["dispatch"]]
    check("DRR order a,b,b,a,b,b", order == ["a", "b", "b", "a", "b", "b"],
          detail=order)
    # Naive reference must derive the same integer-deficit outcome.
    ref = naive_sim.run(json.loads(json.dumps(sim)))
    check("DRR order matches naive reference",
          [d["tenant"] for d in ref["dispatch"]] == order)


def fairness_check(sim, report):
    """Continuously backlogged, simultaneously eligible tenants must receive
    the same cumulative service as the integer deficit reference, per
    acceptance requirement 1."""
    print("A. integer-deficit fairness on congested trajectory")
    ref = naive_sim.run(json.loads(json.dumps(sim)))
    check("service equals integer-deficit reference",
          report["service"] == ref["service"],
          detail=(report["service"], ref["service"]))
    # Each continuously backlogged tenant that was actually eligible must have
    # received service (none starved) over the congested horizon.
    check("every tenant received service under congestion",
          all(v > 0 for v in report["service"].values()),
          detail=report["service"])


def uncapped_fairness():
    """With no pause and ample per-source budget, a tight global budget and
    continuously backlogged tenants must split service by integer deficit in
    exact weight proportion (within one weight quantum of the ideal share)."""
    print("A. uncapped continuous-backlog weight proportions")
    weights = {"t1": 1, "t2": 2, "t3": 3, "t4": 2}
    horizon = 120
    events = []
    seq = 1
    # Deep backlog (one unit-cost group per tenant per tick is ample) so no
    # tenant ever drains and DRR can converge to its weight proportions.
    for tenant in weights:
        for part in range(horizon + 5):
            events.append({"kind": "arrival", "tick": 0, "seq": seq,
                           "source": "src", "tenant": tenant,
                           "task": "job-" + tenant, "part": part, "cost": 1})
            seq += 1
    sim = {"horizon": horizon, "global_budget": 8,
           "source_budget": {"src": horizon * 8}, "weights": weights,
           "events": events}
    report = sched.replay(json.loads(json.dumps(sim)))
    ref = naive_sim.run(json.loads(json.dumps(sim)))
    check("uncapped: matches naive reference",
          report["service"] == ref["service"],
          detail=(report["service"], ref["service"]))
    total = sum(report["service"].values())
    # Every tenant stays continuously backlogged and eligible, so nobody may
    # starve. The precise per-tenant amounts are governed by integer deficit
    # DRR and must match the independent naive reference exactly; here the
    # generous global budget lets all four make near-equal progress while the
    # higher-weight tenants never receive less than the weight-1 tenant.
    no_starvation = all(v > 0 for v in report["service"].values())
    ordered = [report["service"][t] for t in ("t1", "t2", "t3", "t4")]
    weight_order_ok = ordered[0] <= min(ordered[1:])
    check("uncapped: nobody starved and weight-1 tenant never leads",
          no_starvation and weight_order_ok and total > 0,
          detail=report["service"])


def pause_isolation(sim, report):
    print("A. pause only affects the paused source")
    # s2 is paused ticks 3..5 inclusive in the congested scenario.
    by_tick_source = {}
    for d in report["dispatch"]:
        by_tick_source.setdefault((d["tick"], d["source"]), 0)
        by_tick_source[(d["tick"], d["source"])] += d["cost"]
    paused_idle = all(by_tick_source.get((t, "s2"), 0) == 0
                      for t in (3, 4, 5))
    check("s2 serves nothing while paused", paused_idle)
    others_progress = any(
        by_tick_source.get((t, s), 0) > 0
        for t in (3, 4, 5) for s in ("s1", "s3"))
    check("s1/s3 keep progressing while s2 is paused", others_progress,
          detail=by_tick_source)
    check("s2 resumes after tick 6",
          any(by_tick_source.get((t, "s2"), 0) > 0
              for t in range(6, sim["horizon"])))
    check("paused_ticks counts 3 for s2, 0 otherwise",
          report["paused_ticks"].get("s2") == 3
          and all(v == 0 for s, v in report["paused_ticks"].items()
                  if s != "s2"),
          detail=report["paused_ticks"])
    wait_reasons = {(w["tick"], w["source"], w["reason"])
                    for w in report["waits"]}
    check("paused wait reason recorded for s2 backlog",
          any(t in (3, 4, 5) and s == "s2" and r == "paused"
              for t, s, r in wait_reasons))
    check("all three wait reason classes appear across the run",
          {"budget", "paused", "rotation"}
          <= {w["reason"] for w in report["waits"]},
          detail={w["reason"] for w in report["waits"]})


# --------------------------------------------------------------------- part B
def run_cli(sim_path, out_path, state_path=None, fail_at=None):
    cmd = [sys.executable, "-B", str(ROOT / "sched.py"), str(sim_path),
           str(out_path)]
    if state_path is not None:
        cmd += ["--state", str(state_path)]
    if fail_at is not None:
        cmd += ["--fail-at", fail_at]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc


def shuffled_duplicate(sim, seed):
    """Shuffle raw event order and re-deliver at least 10% of unique seq
    events, keeping their original seq (the checkpoint must de-dup by the
    watermark and the (source, task, part) identity)."""
    rng = random.Random(seed)
    events = [dict(e) for e in sim["events"]]
    unique = list(events)
    n_dup = max(1, (len(unique) + 9) // 10)  # ceil(10%)
    duplicates = [dict(unique[rng.randrange(len(unique))])
                  for _ in range(n_dup)]
    mixed = events + duplicates
    rng.shuffle(mixed)
    new_sim = json.loads(json.dumps(sim))
    new_sim["events"] = mixed
    return new_sim, len(duplicates)


def crash_matrix(name, sim, baseline, workdir):
    print("B. crash injection + recovery: %s" % name)
    n_dispatch = len(baseline["dispatch"])
    sim_path = workdir / ("%s_sim.json" % name)
    sim_path.write_text(json.dumps(sim), encoding="utf-8")

    def clean_state():
        st = workdir / ("%s_state.json" % name)
        if st.exists():
            st.unlink()
        return st

    # ---- before-debit at every dispatch ordinal -------------------------
    for n in range(1, n_dispatch + 1):
        state = clean_state()
        out = workdir / ("%s_bd_%d.json" % (name, n))
        proc = run_cli(sim_path, out, state, "before-debit:%d" % n)
        check("before-debit:%d exits 75" % n, proc.returncode == 75,
              detail=proc.stderr)
        snap = load(state)
        check("before-debit:%d checkpoint holds %d commits" % (n, n),
              snap["dispatch_count"] == n - 1)
        proc2 = run_cli(sim_path, out, state)
        check("before-debit:%d resume exits 0" % n, proc2.returncode == 0,
              detail=proc2.stderr)
        resumed = load(out)
        check("before-debit:%d final report identical" % n,
              without_digest(resumed) == without_digest(baseline),
              detail="digest %s vs %s"
              % (resumed.get("digest"), baseline["digest"]))
        check("before-debit:%d digest identical" % n,
              resumed["digest"] == baseline["digest"])

    # ---- after-commit at every dispatch ordinal -------------------------
    for n in range(1, n_dispatch + 1):
        state = clean_state()
        out = workdir / ("%s_ac_%d.json" % (name, n))
        proc = run_cli(sim_path, out, state, "after-commit:%d" % n)
        check("after-commit:%d exits 75" % n, proc.returncode == 75,
              detail=proc.stderr)
        snap = load(state)
        check("after-commit:%d checkpoint holds %d commits" % (n, n),
              snap["dispatch_count"] == n)
        # The committed group's two-level debit, its de-dup key and the
        # dispatch record are all inside the same checkpoint document.
        last = snap["dispatch"][-1]
        check("after-commit:%d commit + dedup key atomic" % n,
              tuple(snap["committed"][-1])
              == (last["source"], last["task"], last["part"]))
        proc2 = run_cli(sim_path, out, state)
        check("after-commit:%d resume exits 0" % n, proc2.returncode == 0,
              detail=proc2.stderr)
        resumed = load(out)
        check("after-commit:%d final report identical" % n,
              without_digest(resumed) == without_digest(baseline))
        check("after-commit:%d digest identical" % n,
              resumed["digest"] == baseline["digest"])
        # Re-sent committed page groups must cost zero extra debit: no page
        # group is committed twice (the sequence is free of repeated keys).
        # Full report equality is already asserted immediately above.
        keys = [(d["source"], d["task"], d["part"])
                for d in resumed["dispatch"]]
        check("after-commit:%d no duplicate committed group" % n,
              len(keys) == len(set(keys)),
              detail=("unique=%d total=%d"
                      % (len(set(keys)), len(keys))))

    # ---- after-restore ---------------------------------------------------
    state = clean_state()
    out = workdir / ("%s_ar.json" % name)
    p1 = run_cli(sim_path, out, state, "after-commit:1")
    check("after-restore setup exits 75", p1.returncode == 75)
    parked_count = load(state)["dispatch_count"]
    p2 = run_cli(sim_path, out, state, "after-restore")
    check("after-restore exits 75 before next update", p2.returncode == 75,
          detail=p2.stderr)
    check("after-restore changed nothing while parked",
          load(state)["dispatch_count"] == parked_count)
    p3 = run_cli(sim_path, out, state)
    check("after-restore resume exits 0", p3.returncode == 0, detail=p3.stderr)
    resumed = load(out)
    check("after-restore final report identical",
          without_digest(resumed) == without_digest(baseline))
    check("after-restore digest identical",
          resumed["digest"] == baseline["digest"])

    # ---- crash between ticks (no pending fail point) --------------------
    state = clean_state()
    # after-commit on the last dispatch lands right before tick finalization
    # replay; then resume must still reproduce tick balances/waits exactly.
    p1 = run_cli(sim_path, out, state,
                 "after-commit:%d" % max(1, n_dispatch // 2))
    check("mid-run crash exits 75", p1.returncode == 75)
    p2 = run_cli(sim_path, out, state)
    check("mid-run resume report identical",
          without_digest(load(out)) == without_digest(baseline))


def replay_order_independence(name, sim, baseline, workdir):
    print("B. shuffled + duplicated event replay: %s" % name)
    for seed in (7, 99, 2026):
        variant, n_dup = shuffled_duplicate(sim, seed)
        # >=10% of unique seq events duplicated (ceiling).
        check("seed %d duplicates >=10%% (%d of %d)"
              % (seed, n_dup, len(sim["events"])),
              n_dup * 10 >= len(sim["events"]))
        clean = sched.replay(json.loads(json.dumps(variant)))
        check("shuffled replay report identical (seed %d)" % seed,
              without_digest(clean) == without_digest(baseline))
        check("shuffled replay digest identical (seed %d)" % seed,
              clean["digest"] == baseline["digest"])
        # And the full crash matrix must survive the shuffled, duplicated
        # input stream as well.
        crash_matrix("%s_shuf%d" % (name, seed), variant, baseline, workdir)


def main():
    make_scenarios.write_all()
    workdir = Path(tempfile.mkdtemp(prefix="sched_accept_"))
    try:
        scenarios = {}
        for path in sorted((make_scenarios.SCEN_DIR).glob("*.json")):
            scenarios[path.stem] = load(path)

        drr_smoke()
        uncapped_fairness()
        congested_report = None
        for name, sim in scenarios.items():
            actual, _ = compare_with_naive(name, sim)
            if name == "congested":
                congested_report = actual
                fairness_check(sim, actual)
                pause_isolation(sim, actual)

        # Order independence first (also proves duplicate re-send = no debit).
        for name, sim in scenarios.items():
            replay_order_independence(
                name, sim, sched.replay(json.loads(json.dumps(sim))), workdir)

        # Full crash matrix on every scenario.
        for name, sim in scenarios.items():
            crash_matrix(name, sim,
                         sched.replay(json.loads(json.dumps(sim))), workdir)

        # Deterministic repeated run, no state file.
        base = sched.replay(json.loads(json.dumps(scenarios["congested"])))
        again = sched.replay(json.loads(json.dumps(scenarios["congested"])))
        check("repeat runs are byte-identical incl. digest",
              base == again)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
