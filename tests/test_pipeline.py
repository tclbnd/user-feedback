#!/usr/bin/env python3
"""Acceptance tests for the backpressure pipeline simulator.

Run with ``PYTHONDONTWRITEBYTECODE=1 python3 tests/test_pipeline.py``.
Only the standard library is used so the suite works in the same environment
as ``pipeline.py``.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline  # noqa: E402

BASE = {
    "stages": [{"name": "src", "cap": 8}, {"name": "mid", "cap": 4}, {"name": "snk", "cap": 4}],
    "items": 40,
    "stall": {"stage": "snk", "from": 10, "to": 20},
    "concurrency": 1,
    "seed": 1,
}

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s  %s" % (name, detail))
        FAILURES.append(name)


def scenario(**overrides):
    sc = json.loads(json.dumps(BASE))
    sc.update(overrides)
    return sc


def test_no_loss_and_order():
    print("stall injection: zero loss, in-order catch-up")
    for conc in (1, 2, 4, 8):
        sim = pipeline.simulate(scenario(concurrency=conc))
        r, m = sim["result"], sim["metrics"]
        check("conc=%d dropped==0" % conc, r["dropped"] == 0, r["dropped"])
        check("conc=%d throughput==items" % conc, r["throughput"] == BASE["items"], r["throughput"])
        check("conc=%d order_ok" % conc, r["order_ok"] is True)
        check("conc=%d done is 0..n-1" % conc,
              sim["trace"]["done"] == list(range(BASE["items"])), sim["trace"]["done"][:20])
        check("conc=%d no deadlock under stall" % conc, r["deadlock"] is False)
        for name, level_list in r["water_levels"].items():
            cap = next(s["cap"] for s in BASE["stages"] if s["name"] == name)
            check("conc=%d %s bound<=%d" % (conc, name, cap), max(level_list) <= cap, max(level_list))
            check("conc=%d %s length==ticks" % (conc, name), len(level_list) == r["ticks"])
        check("conc=%d backpressure recorded upstream" % conc,
              r["blocked_ticks"]["mid"] > 0 and r["blocked_ticks"]["src"] > 0,
              r["blocked_ticks"])
        check("conc=%d generations ordered" % conc, m["generation_order_ok"] is True)


def test_recovery_order():
    print("recovery walks downstream -> upstream")
    sim = pipeline.simulate(scenario(concurrency=2))
    recovery = sim["metrics"]["recovery"]
    check("recovery reports the stall end", recovery["stall_end"] == 20, recovery["stall_end"])
    seq = recovery["sequence"]
    check("recovery names every disturbed stage",
          seq == ["snk", "mid", "src"], seq)
    ticks = [recovery["first_forward_tick"][name] for name in seq]
    check("recovery timestamps non-decreasing", ticks == sorted(ticks), ticks)
    check("no stage moves before the stall ends", min(ticks) >= 20, ticks)


def test_recovery_order_mid_pipeline():
    print("a mid-pipeline pause recovers downstream -> upstream, in order")
    sim = pipeline.simulate({
        "stages": [{"name": "s%d" % i, "cap": 4} for i in range(6)],
        "items": 120,
        "concurrency": 4,
        "stall": {"stage": "s3", "from": 60, "to": 90},
    })
    r, m = sim["result"], sim["metrics"]
    check("only disturbed stages report recovery",
          m["recovery"]["sequence"] == ["s3", "s2", "s1", "s0"],
          m["recovery"]["sequence"])
    check("tiers below the pause are untouched",
          m["stages"]["s4"]["blocked_ticks"] == 0 and
          m["stages"]["s5"]["blocked_ticks"] == 0,
          {k: m["stages"][k]["blocked_ticks"] for k in ("s4", "s5")})
    check("mid-pipeline pause loses nothing",
          r["dropped"] == 0 and r["throughput"] == 120, (r["dropped"], r["throughput"]))
    check("mid-pipeline pause preserves order", r["order_ok"] is True)
    check("mid-pipeline pause is not a deadlock", r["deadlock"] is False)
    check("every buffer respects its cap",
          all(max(v or [0]) <= 4 for v in r["water_levels"].values()),
          {k: max(v or [0]) for k, v in r["water_levels"].items()})


def test_water_levels_trace_stall():
    print("water levels reflect the stall and the drain afterwards")
    sim = pipeline.simulate(scenario(concurrency=1))
    r = sim["result"]
    snk, mid, src = r["water_levels"]["snk"], r["water_levels"]["mid"], r["water_levels"]["src"]
    check("snk filled during stall", max(snk[10:20]) == 4, snk[10:20])
    check("snk drains after resume", snk[20] < 4 or max(snk[20:]) < 4, snk[20:25])
    check("mid sees pressure", max(mid) == 4, max(mid))
    check("src level bounded by cap", max(src) <= 8, max(src))
    check("levels drop back to 0 at the end", snk[-1] == 0 and mid[-1] == 0 and src[-1] == 0,
          (snk[-1], mid[-1], src[-1]))


def test_deadlock_detection():
    print("deadlock detector: silent when healthy, loud when wedged")
    healthy = pipeline.simulate(scenario())
    check("no false positive without stall", healthy["result"]["deadlock"] is False)

    wedged = {
        "stages": [{"name": "a", "cap": 2}, {"name": "b", "cap": 0}],
        "items": 5,
        "concurrency": 1,
    }
    hung = pipeline.simulate(wedged)
    check("detects a genuinely wedged pipeline", hung["result"]["deadlock"] is True)

    long_stall = scenario(stall={"stage": "snk", "from": 0, "to": 60})
    slow = pipeline.simulate(long_stall)
    check("no false positive during a long stall", slow["result"]["deadlock"] is False)


def test_rejection_policies():
    print("configurable, metered rejection policies")
    base = {
        "stages": [{"name": "a", "cap": 2}, {"name": "b", "cap": 1}, {"name": "c", "cap": 1}],
        "items": 10,
        # The sink pauses so the bounded buffers between stages actually fill.
        "stall": {"stage": "c", "from": 2, "to": 6},
        "concurrency": 2,
    }

    newest = pipeline.simulate(dict(base, reject_policy="drop_newest"))
    check("drop_newest drops", newest["result"]["dropped"] > 0, newest["result"]["dropped"])
    check("drop_newest keeps order", newest["result"]["order_ok"] is True)
    check("drop_newest metered",
          sum(s["dropped"] for s in newest["metrics"]["stages"].values()) == newest["result"]["dropped"],
          {k: v["dropped"] for k, v in newest["metrics"]["stages"].items()})

    oldest = pipeline.simulate(dict(base, reject_policy="drop_oldest"))
    check("drop_oldest drops", oldest["result"]["dropped"] > 0, oldest["result"]["dropped"])
    check("drop_oldest evicts from head",
          sum(s["evicted"] for s in oldest["metrics"]["stages"].values()) ==
          oldest["result"]["dropped"],
          {k: s["evicted"] for k, s in oldest["metrics"]["stages"].items()})

    blocked = pipeline.simulate(dict(base, reject_policy="block"))
    check("block never drops", blocked["result"]["dropped"] == 0)
    check("block delivers everything", blocked["result"]["throughput"] == base["items"],
          blocked["result"]["throughput"])

    mixed = {
        "stages": [
            {"name": "a", "cap": 2},
            {"name": "b", "cap": 1},
            {"name": "c", "cap": 1, "reject_policy": "drop_newest"},
        ],
        "items": 10,
        "stall": {"stage": "c", "from": 2, "to": 6},
        "concurrency": 2,
        "reject_policy": "block",
    }
    m = pipeline.simulate(mixed)
    check("per-stage policy overrides global", m["result"]["dropped"] > 0, m["result"]["dropped"])



def test_waterline_hysteresis_and_headroom():
    print("waterline protocol: hysteresis and reserved head-room")
    stage = pipeline.Stage(name="q", cap=4, high=2, low=1,
                           policy=pipeline.BLOCK, priority_threshold=5)
    buf = pipeline.BoundedBuffer(stage)
    item = lambda i, p=0: pipeline.Item(seq=i, value=i, priority=p)  # noqa: E731

    check("ordinary traffic fills up to the high watermark",
          buf.offer(item(0)) == pipeline.ACCEPTED and
          buf.offer(item(1)) == pipeline.ACCEPTED and len(buf) == 2, len(buf))
    check("pressure latches at the high watermark", buf.pressure is True)
    check("ordinary traffic is blocked above the watermark",
          buf.offer(item(2)) == pipeline.BLOCKED and len(buf) == 2, len(buf))
    check("priority traffic may use the reserved head-room",
          buf.offer(item(3, 9)) == pipeline.ACCEPTED and
          buf.offer(item(4, 9)) == pipeline.ACCEPTED and len(buf) == 4, len(buf))
    check("even priority traffic respects the hard cap",
          buf.offer(item(5, 9)) == pipeline.BLOCKED and len(buf) == 4, len(buf))
    check("peak never exceeds cap", buf.peak == 4, buf.peak)
    buf.pop()
    check("draining one slot does not release pressure (hysteresis)",
          buf.pressure is True)
    buf.pop()
    buf.pop()
    check("pressure releases only at the low watermark",
          len(buf) == 1 and buf.pressure is False, (len(buf), buf.pressure))
    check("ordinary traffic resumes after release",
          buf.offer(item(6)) == pipeline.ACCEPTED)
    check("would_admit agrees with offer on a full buffer",
          buf.would_admit(item(7)) is False and buf.offer(item(7)) == pipeline.BLOCKED)


def test_priority_admission():
    print("priority admission into reserved head-room")
    sc = {
        "stages": [
            {"name": "src", "cap": 2},
            {"name": "snk", "cap": 4, "high": 2, "low": 1, "priority_threshold": 5},
        ],
        "items": 12,
        "priorities": [0, 0, 9, 0, 0, 9, 0, 0, 9, 0, 0, 0],
        "stall": {"stage": "snk", "from": 2, "to": 8},
    }
    sim = pipeline.simulate(sc)
    r = sim["result"]
    check("priority run keeps order", r["order_ok"] is True, sim["trace"]["done"])
    check("priority run keeps bound", max(r["water_levels"]["snk"]) <= 4, max(r["water_levels"]["snk"]))
    check("high-priority items delivered", all(v in sim["trace"]["done"] for v in (2, 5, 8)))


def test_slow_consumer_is_bypassed():
    print("a stalled consumer is isolated from the rest of the pipeline")
    # ``mid`` pauses.  The sweep must keep visiting the tiers below it, so
    # whatever ``snk`` already holds still completes, and only the paused
    # stage plus its producers accumulate pressure.  A scheduler that aborted
    # the sweep at the first blocked stage would freeze ``snk`` instead.
    sc = {
        "stages": [
            {"name": "src", "cap": 8},
            {"name": "mid", "cap": 4},
            {"name": "snk", "cap": 4},
        ],
        "items": 25,
        "concurrency": 4,
        "stall": {"stage": "mid", "from": 10, "to": 30},
    }
    sim = pipeline.simulate(sc)
    r, metrics = sim["result"], sim["metrics"]
    check("stalled stage is accounted as stalled, not blocked",
          metrics["stages"]["mid"]["stalled_ticks"] == 20 and
          metrics["stages"]["mid"]["blocked_ticks"] == 0,
          metrics["stages"]["mid"])
    check("stages after the pause are never blocked",
          metrics["stages"]["snk"]["blocked_ticks"] == 0 and
          metrics["stages"]["snk"]["stalled_ticks"] == 0,
          metrics["stages"]["snk"])
    check("upstream of the pause takes the backpressure", r["blocked_ticks"]["src"] > 0,
          r["blocked_ticks"])
    check("queue below the pause keeps draining (empty at pause end)",
          r["water_levels"]["snk"][29] == 0, r["water_levels"]["snk"][25:31])
    check("stalled queue saturates at its cap", max(r["water_levels"]["mid"]) == 4,
          max(r["water_levels"]["mid"]))
    check("nothing is lost while bypassed", r["dropped"] == 0 and r["throughput"] == 25,
          (r["dropped"], r["throughput"]))
    check("bounded even while bypassed",
          all(max(v) <= c for v, c in zip(r["water_levels"].values(), (8, 4, 4))),
          {k: max(v) for k, v in r["water_levels"].items()})
    check("order preserved across the pause", r["order_ok"] is True)
    check("no deadlock reported for the pause", r["deadlock"] is False)

    # An indefinitely paused stage is still not a deadlock (the pause is a
    # declared, recoverable fault) but it must not lose or reorder anything.
    paused = pipeline.simulate(dict(sc, stall={"stage": "mid", "from": 5, "to": 10000}))
    check("endless pause is not misreported as a deadlock", paused["result"]["deadlock"] is False)
    check("endless pause loses nothing", paused["result"]["dropped"] == 0)
    check("endless pause keeps every buffer bounded",
          all(max(v) <= c for v, c in zip(paused["result"]["water_levels"].values(), (8, 4, 4))),
          {k: max(v) for k, v in paused["result"]["water_levels"].items()})


def test_configuration_edges():
    print("configuration edges stay safe")
    r = pipeline.simulate({"stages": [], "items": 5})["result"]
    check("empty topology is trivially fine",
          r == {"dropped": 0, "deadlock": False, "water_levels": {},
                "blocked_ticks": {}, "throughput": 0, "order_ok": True, "ticks": 0}, r)

    r = pipeline.simulate({"stages": [{"name": "a", "cap": 2}], "items": 0})["result"]
    check("zero items finishes immediately", r["ticks"] == 0 and r["throughput"] == 0, r)

    dup = pipeline.simulate({
        "stages": [{"name": "a", "cap": 2}, {"name": "a", "cap": 2}],
        "items": 4,
    })["result"]
    check("duplicate stage names are disambiguated",
          sorted(dup["water_levels"]) == ["a", "a#2"] and
          sorted(dup["blocked_ticks"]) == ["a", "a#2"], dup["water_levels"].keys())
    check("duplicate-name run still delivers in order",
          dup["throughput"] == 4 and dup["order_ok"] is True)

    weird = pipeline.simulate({
        "stages": [{"name": "a", "cap": "x"}, {"name": "b", "cap": 2, "high": 99, "low": -5}],
        "items": 6,
        "concurrency": 0,
        "stall": {"stage": "nope", "from": 1, "to": 5},
        "reject_policy": "not-a-policy",
    })["result"]
    check("malformed numbers fall back to defaults",
          weird["throughput"] == 6 and weird["dropped"] == 0, weird["throughput"])
    check("unknown stall target does not freeze the pipeline",
          weird["deadlock"] is False and weird["ticks"] == 6, weird["ticks"])
    check("watermarks are clamped into [0, cap]",
          max(weird["water_levels"]["b"]) <= 2, max(weird["water_levels"]["b"]))

    multi = pipeline.simulate({
        "stages": [{"name": "a", "cap": 4}, {"name": "b", "cap": 4}],
        "items": 20,
        "concurrency": 2,
        "stalls": [{"stage": "b", "from": 2, "duration": 6}],
    })["result"]
    check("stall windows accept a duration form",
          multi["throughput"] == 20 and multi["dropped"] == 0 and
          multi["deadlock"] is False, multi)


def test_concurrency_equivalence():
    print("throughput and inter-stage order are invariant under concurrency")
    reference = None
    for conc in (2, 4, 8):
        sim = pipeline.simulate(scenario(concurrency=conc))
        r = sim["result"]
        check("conc=%d delivers every item" % conc, r["throughput"] == BASE["items"], r["throughput"])
        check("conc=%d ordered" % conc, sim["trace"]["done"] == list(range(BASE["items"])))
        if reference is None:
            reference = sim["trace"]["done"]
        else:
            check("conc=%d same completion order as reference" % conc,
                  sim["trace"]["done"] == reference)
    # Determinism: identical input -> identical output.
    a = pipeline.simulate(scenario(concurrency=4))
    b = pipeline.simulate(scenario(concurrency=4))
    check("simulation is deterministic", a["result"] == b["result"])


def test_cli_roundtrip():
    print("CLI matches the documented contract")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "scenario.json")
        dst = os.path.join(tmp, "output.json")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump(scenario(), fh)
        rc = pipeline.main([src, dst])
        check("exit code 0", rc == 0, rc)
        with open(dst, encoding="utf-8") as fh:
            out = json.load(fh)
    expected = {"dropped", "deadlock", "water_levels", "blocked_ticks",
                "throughput", "order_ok", "ticks"}
    check("output keys match README", set(out) == expected, set(out) ^ expected)
    check("dropped is 0", out["dropped"] == 0)
    check("deadlock is false", out["deadlock"] is False)
    check("throughput is 40", out["throughput"] == 40, out["throughput"])
    check("blocked_ticks has every stage", set(out["blocked_ticks"]) == {"src", "mid", "snk"},
          out["blocked_ticks"])
    check("water_levels has every stage", set(out["water_levels"]) == {"src", "mid", "snk"},
          set(out["water_levels"]))
    check("levels and ticks agree",
          all(len(v) == out["ticks"] for v in out["water_levels"].values()),
          {k: len(v) for k, v in out["water_levels"].items()})


def test_reference_equivalence():
    print("reference behaviour: linear pipeline without a pause")
    # With no stall and enough capacity the pipeline is a plain conveyor: one
    # item per stage per tick, so a 3-stage run of N items needs N ticks and
    # yields the items in injection order.  Concurrency changes how fast the
    # source fills the head buffer, never the per-tick drain rate.
    ref = pipeline.simulate({"stages": scenario()["stages"], "items": 40, "concurrency": 1})
    for conc in (2, 4, 8):
        sim = pipeline.simulate(
            {"stages": scenario()["stages"], "items": 40, "concurrency": conc})
        check("conc=%d same tick count" % conc,
              sim["result"]["ticks"] == ref["result"]["ticks"],
              (sim["result"]["ticks"], ref["result"]["ticks"]))
        check("conc=%d same throughput" % conc,
              sim["result"]["throughput"] == ref["result"]["throughput"])
        check("conc=%d same completion order" % conc,
              sim["trace"]["done"] == ref["trace"]["done"])
    # Inter-stage order: every stage observes items in strictly increasing
    # sequence order; the sink order is the injection order.
    sim = pipeline.simulate({"stages": scenario()["stages"], "items": 40, "concurrency": 4})
    check("sink order is the injection order",
          sim["trace"]["done"] == list(range(40)))
    check("generations never decrease", sim["trace"]["generations"] ==
          sorted(sim["trace"]["generations"]))


def test_bounds_never_exceeded():
    print("buffer occupancy never exceeds its cap")
    cases = [
        scenario(),
        scenario(concurrency=8),
        {"stages": [{"name": "a", "cap": 1}, {"name": "b", "cap": 1},
                    {"name": "c", "cap": 1}], "items": 30, "concurrency": 8,
         "stall": {"stage": "c", "from": 0, "to": 15}},
        {"stages": [{"name": "a", "cap": 3, "high": 2, "low": 1},
                    {"name": "b", "cap": 2, "high": 1, "low": 0}],
         "items": 15, "concurrency": 4, "priorities": 9,
         "stall": {"stage": "b", "from": 2, "to": 12}},
    ]
    for sc in cases:
        sim = pipeline.simulate(sc)
        caps = {s["name"]: s["cap"] for s in sc["stages"]}
        for name, series in sim["result"]["water_levels"].items():
            check("%s bound <= %d" % (name, caps[name]),
                  max(series or [0]) <= caps[name], max(series or [0]))
            check("%s recorded peak <= %d" % (name, caps[name]),
                  sim["metrics"]["stages"][name]["peak"] <= caps[name])


def main():
    tests = [
        test_no_loss_and_order,
        test_recovery_order,
        test_recovery_order_mid_pipeline,
        test_water_levels_trace_stall,
        test_deadlock_detection,
        test_rejection_policies,
        test_waterline_hysteresis_and_headroom,
        test_priority_admission,
        test_slow_consumer_is_bypassed,
        test_configuration_edges,
        test_concurrency_equivalence,
        test_cli_roundtrip,
        test_reference_equivalence,
        test_bounds_never_exceeded,
    ]
    for test in tests:
        test()
    print()
    if FAILURES:
        print("%d check(s) failed:" % len(FAILURES))
        for name in FAILURES:
            print("  - %s" % name)
        return 1
    print("all checks passed")
    return 0

if __name__ == "__main__":
    sys.exit(main())
