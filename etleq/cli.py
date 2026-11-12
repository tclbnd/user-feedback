"""Command line entry point for the etleq equivalence checker.

Usage
-----
Check one equivalence case (a JSON document with ``input``, ``old``, ``new``)::

    python3 -m etleq.cli path/to/case.json

Print a stable, machine-readable JSON verdict (exit code 0 on equivalent or
different; 3 on unknown; 2 on an unsupported/out-of-scope rule)::

    python3 -m etleq.cli case.json --json

The verdict always carries the solver statistics (expanded states, pruned
infeasible branches, solver calls) and, for a ``different`` result, the
deterministic minimal witness with each version's field values, error class and
branch position, replayed through the independent concrete interpreter.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .check import EquivalenceChecker, Verdict
from .parser import RuleFormatError, UnsupportedRule, parse_case
from .solver.native import NativeSolver


def _build_solver(schema, branch_budget: int):
    return NativeSolver(schema, branch_budget=branch_budget)


def check_document(doc: dict, branch_budget: int,
                   enumeration_cap: int) -> Verdict:
    old, new = parse_case(doc)
    checker = EquivalenceChecker(
        old, new,
        lambda schema: _build_solver(schema, branch_budget),
        enumeration_cap=enumeration_cap,
    )
    return checker.run()


def _print_human(verdict: Verdict, path: str) -> None:
    print(f"case: {path}")
    print(f"verdict: {verdict.status}")
    if verdict.reason:
        print(f"reason: {verdict.reason}")
    if verdict.witness is not None:
        print(f"minimal witness: {json.dumps(verdict.witness.to_json(), sort_keys=True)}")
        for diff in verdict.diffs:
            print(f"  field {diff.field!r}:")
            print(f"    old: {json.dumps(diff.old, sort_keys=True)}")
            print(f"    new: {json.dumps(diff.new, sort_keys=True)}")
            if diff.old_branch or diff.new_branch:
                print(f"    old branch: {json.dumps(diff.old_branch)}")
                print(f"    new branch: {json.dumps(diff.new_branch)}")
    if verdict.proof:
        print("proof:")
        for key, value in verdict.proof.items():
            print(f"  {key}: {value}")
    stats = verdict.stats
    if stats:
        print("statistics:")
        for side in ("old", "new"):
            s = stats.get(side, {})
            print(f"  {side}: states={s.get('states')} peak={s.get('states_peak')} "
                  f"forks={s.get('forks')} merges={s.get('merges')} "
                  f"pruned={s.get('pruned_branches')} unrolls={s.get('loop_unrolls')} "
                  f"solver_calls={s.get('solver_calls')}")
        if "enumerated_inputs" in stats:
            print(f"  enumerated_inputs: {stats['enumerated_inputs']}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="etleq",
        description="Symbolic equivalence checker for finite ETL rule versions.")
    parser.add_argument("case", help="path to an equivalence-case JSON document")
    parser.add_argument("--json", action="store_true",
                        help="emit the verdict as JSON instead of text")
    parser.add_argument("--branch-budget", type=int, default=200_000,
                        help="DPLL decision budget before returning unknown")
    parser.add_argument("--enumeration-cap", type=int, default=4096,
                        help="domain size under which concrete enumeration is the "
                             "authoritative oracle")
    args = parser.parse_args(argv)

    with open(args.case) as fh:
        doc = json.load(fh)

    try:
        verdict = check_document(doc, args.branch_budget, args.enumeration_cap)
    except UnsupportedRule as exc:
        print(f"unsupported rule (returning unknown-class): {exc}", file=sys.stderr)
        return 2
    except RuleFormatError as exc:
        print(f"malformed rule document: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(verdict.to_json(), indent=2, sort_keys=True))
    else:
        _print_human(verdict, args.case)

    if verdict.status == "unknown":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
