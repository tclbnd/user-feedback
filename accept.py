#!/usr/bin/env python3
"""Acceptance report for the backpressure pipeline.

Prints the two acceptance criteria on one screen: a downstream pause with the
zero-loss / ordered-catch-up / no-false-deadlock checks, and the 2/4/8
concurrency regression.  Exit status is non-zero on any violation.

    PYTHONDONTWRITEBYTECODE=1 python3 accept.py
"""

import sys

import pipeline

SCENARIO = {
    "stages": [
        {"name": "src", "cap": 8},
        {"name": "mid", "cap": 4},
        {"name": "snk", "cap": 4},
    ],
    "items": 40,
    "stall": {"stage": "snk", "from": 10, "to": 20},
    "concurrency": 1,
    "seed": 1,
}

FAILED = []


def line(label, passed, detail=""):
    if not passed:
        FAILED.append(label)
    print("  %-4s %-46s %s" % ("PASS" if passed else "FAIL", label, detail))


def report(scenario):
    sim = pipeline.simulate(scenario)
    result, metrics = sim["result"], sim["metrics"]
    caps = {s["name"]: s["cap"] for s in scenario["stages"]}
    trace = sim["trace"]["done"]
    line("dropped == 0 (retry channel keeps data)", result["dropped"] == 0, result["dropped"])
    line("deadlock not misreported", result["deadlock"] is False, result["deadlock"])
    line("order preserved (sequence + generation)", result["order_ok"] is True)
    line("all injected items delivered", result["throughput"] == scenario["items"],
         "%d/%d" % (result["throughput"], scenario["items"]))
    line("completion order is 0..n-1", trace == list(range(scenario["items"])), trace[-3:])
    line("water levels within cap",
         all(max(v or [0]) <= caps[k] for k, v in result["water_levels"].items()),
         {k: max(v or [0]) for k, v in result["water_levels"].items()})
    sequence = metrics["recovery"]["sequence"]
    if sequence:
        line("recovery order downstream -> upstream",
             sequence[0] == "snk" and sequence[-1] == "src", " -> ".join(sequence))
    line("per-stage metrics present", set(metrics["stages"]) == set(caps),
         {k: v["dropped"] for k, v in metrics["stages"].items()})
    return result, metrics, trace


def main():
    print("=" * 78)
    print("acceptance 1: downstream pause, zero loss, ordered catch-up")
    print("=" * 78)
    result, metrics, _ = report(SCENARIO)
    print("  water levels around the pause")
    for name in ("src", "mid", "snk"):
        print("    %-4s %s" % (name, result["water_levels"][name][8:22]))
    print("  blocked ticks:", result["blocked_ticks"])
    print("  recovery:", metrics["recovery"]["sequence"],
          metrics["recovery"]["first_forward_tick"])

    print()
    print("=" * 78)
    print("acceptance 2: concurrency 2/4/8 -- throughput and inter-stage order")
    print("=" * 78)
    reference = None
    for conc in (2, 4, 8):
        scenario = dict(SCENARIO, concurrency=conc)
        scenario.pop("stall")
        print("  concurrency=%d" % conc)
        res, _met, trace = report(scenario)
        if reference is None:
            reference = (res, trace)
            continue
        ref_result, ref_trace = reference
        line("same ticks as reference", res["ticks"] == ref_result["ticks"],
             "%d vs %d" % (res["ticks"], ref_result["ticks"]))
        line("same throughput as reference",
             res["throughput"] == ref_result["throughput"])
        line("same completion order as reference", trace == ref_trace)

    print()
    print("RESULT:", "FAIL" if FAILED else "PASS")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
