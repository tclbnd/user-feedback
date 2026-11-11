"""Command line entry point for equivalence checking.

Usage::

    python3 -I scripts/check_rule.py CASE.json [--budget N] [--json]

``CASE.json`` is a rule case document with ``input`` plus ``old``/``new``
sections (the same shape as fixtures/price-rules.json).  The default output
is a human-readable report; ``--json`` emits a stable, machine-readable
document (witnesses ordered by input field order).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl_eq import parse_case, check_equivalence, EQUIVALENT, NOT_EQUIVALENT, UNDECIDABLE
from etl_eq.semantics import MISSING, NULL


def _display(v):
    if v is MISSING:
        return "MISSING"
    if v is NULL:
        return "NULL"
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description="Symbolic ETL rule equivalence checker")
    ap.add_argument("case_file", help="JSON rule case (input + old + new)")
    ap.add_argument("--budget", type=int, default=200_000,
                    help="solver search budget (default 200000)")
    ap.add_argument("--json", action="store_true", help="emit JSON report")
    args = ap.parse_args(argv)

    with open(args.case_file) as fh:
        doc = json.load(fh)
    old, new = parse_case(doc)
    report = check_equivalence(old, new, budget=args.budget)

    if args.json:
        payload = {
            "verdict": report.verdict,
            "domain_size": report.domain_size,
            "feasible_pairs": report.feasible_pairs,
            "infeasible_pairs": report.infeasible_pairs,
            "undecidable_reason": report.undecidable_reason,
            "old_execution": _exec_json(report.old_execution),
            "new_execution": _exec_json(report.new_execution),
            "witnesses": [
                {
                    "record": {k: _display(v) for k, v in
                               sorted(w.record.items(), key=lambda kv: kv[0])},
                    "old": w.old.as_jsonable(),
                    "new": w.new.as_jsonable(),
                    "old_locations": list(w.old_locations),
                    "new_locations": list(w.new_locations),
                    "differences": w.differences,
                }
                for w in report.witnesses
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report.verdict != UNDECIDABLE else 2

    print(f"verdict        : {report.verdict}")
    print(f"input domain   : {report.domain_size} supported inputs")
    print(f"state pairs    : {report.feasible_pairs} feasible, "
          f"{report.infeasible_pairs} infeasible")
    for label, exe in (("old", report.old_execution), ("new", report.new_execution)):
        if exe:
            print(f"{label:3} execution  : {len(exe.states)} terminal states, "
                  f"{exe.pruned_branches} pruned, {exe.merged_states} merged, "
                  f"{exe.solver_calls} solver calls, "
                  f"max {exe.max_live_states} live, {exe.iterations} loop iterations")
    if report.verdict == UNDECIDABLE:
        print("reason         :", report.undecidable_reason)
        return 2
    for w in report.witnesses:
        print("-" * 60)
        print("minimal differing input:",
              ", ".join(f"{k}={_display(v)}" for k, v in sorted(w.record.items())))
        print("  old:", w.old.as_jsonable())
        print("  new:", w.new.as_jsonable())
        print("  old branch positions:", list(w.old_locations))
        print("  new branch positions:", list(w.new_locations) )
        for d in w.differences:
            print("  difference:", d)
    if report.verdict == EQUIVALENT:
        print("Proven equal over the complete input domain "
              f"({report.domain_size} inputs); no differing input exists.")
    return 0


def _exec_json(exe):
    if exe is None:
        return None
    return {
        "terminal_states": len(exe.states),
        "pruned_branches": exe.pruned_branches,
        "merged_states": exe.merged_states,
        "solver_calls": exe.solver_calls,
        "max_live_states": exe.max_live_states,
        "loop_iterations": exe.iterations,
    }


if __name__ == "__main__":
    sys.exit(main())
