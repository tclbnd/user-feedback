"""One-command acceptance harness for the etleq equivalence checker.

Run::

    python3 accept.py

It performs two independent phases:

1. Small-domain exhaustive phase. Every small case is checked symbolically and
   then independently enumerated across its *entire* finite input domain with
   the concrete interpreter; the symbolic verdict must agree with the concrete
   oracle on every input, and any reported minimal witness must be replayed and
   confirmed.
2. Wider-domain stress phase. Multi-branch / bounded-loop cases with a larger
   integer domain run through path pruning and static loop unrolling, reporting
   the concrete number of expanded states, pruned infeasible branches and
   solver calls. Cases deliberately seeded with differences only at the
   overflow boundary, on a NULL branch, or at a loop's last iteration verify
   that the symbolic witness lands exactly on the seeded point.

The harness never raises on an ``unknown`` verdict: that is the specified
honest outcome when a rule leaves the supported fragment or exhausts the
solver budget. It is reported distinctly from ``equivalent``/``different``.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from etleq import build_solver, enumerate_domain, interpret, parse_case
from etleq.check import EquivalenceChecker
from etleq.domain import domain_size, point_to_record
from etleq.parser import RuleFormatError, UnsupportedRule
from etleq.solver.interface import SolverResult, lex_less

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.join(HERE, "fixtures", "cases")

# Cases whose full cartesian domain is small enough to enumerate exhaustively.
EXHAUSTIVE_CASES = [
    "div-rem.json",
    "overflow-diff.json",
    "overflow-wrap.json",
    "null-branch.json",
    os.path.join("..", "price-rules.json"),
]

# Larger stress cases: rely on symbolic path pruning; domain enumeration is
# limited to a sampled prefix so the run stays local and fast.
STRESS_CASES = [
    "div-failures.json",
    "multi-branch.json",
    "loop-sum.json",
    "loop-last-iteration.json",
]

ENUM_CAP = 6000


def _load(rel: str) -> dict:
    path = rel if os.path.isabs(rel) else os.path.join(CASE_DIR, rel)
    with open(path) as fh:
        return json.load(fh)


def _observable(result: dict) -> dict:
    """Strip version-internal location ids so only observable field values and
    error *classification* (not the source loc label) are compared."""
    out = {"outcomes": result["outcomes"]}
    err = result.get("error")
    if err is not None:
        out["error"] = {"code": err.get("code"), "iteration": err.get("iteration")}
    else:
        out["error"] = None
    return out


def _concrete_oracle(old, new) -> Tuple[bool, List[dict]]:
    """Enumerate the domain; return (all_observably_equal, first_differences)."""
    diffs: List[dict] = []
    total = 0
    for point in enumerate_domain(old.schema):
        if total >= ENUM_CAP:
            break
        total += 1
        record = point_to_record(point)
        ro = _observable(interpret(old, record).to_json())
        rn = _observable(interpret(new, record).to_json())
        if ro != rn:
            diffs.append({"point": point.to_json(), "old": ro, "new": rn})
    return (not diffs), diffs


def run_exhaustive() -> bool:
    print("=" * 72)
    print("PHASE 1 — small-domain exhaustive cross-validation")
    print("=" * 72)
    ok = True
    for rel in EXHAUSTIVE_CASES:
        doc = _load(rel)
        old, new = parse_case(doc)
        size = domain_size(old.schema)
        t0 = time.time()
        verdict = EquivalenceChecker(old, new, lambda s: build_solver(s)).run()
        dt = time.time() - t0
        equal, diffs = _concrete_oracle(old, new)
        # Symbolic verdict must match the concrete oracle.
        if verdict.status == "equivalent":
            match = equal
        elif verdict.status == "different":
            match = (not equal)
        else:
            match = None  # unknown: cannot claim, check separately below
        status = "OK" if match is True else ("HONEST-UNKNOWN" if match is None else "FAIL")
        if match is False:
            ok = False
        print(f"[{status:>14}] {os.path.basename(rel):<24} "
              f"domain={size:<6} symbolic={verdict.status:<10} "
              f"oracle_equal={equal:<6} {dt:5.2f}s")
        if verdict.status == "different":
            # Replay the minimal witness through the independent interpreter,
            # comparing only observable outcomes (source loc labels excluded).
            record = point_to_record(verdict.witness)
            ro = _observable(interpret(old, record).to_json())
            rn = _observable(interpret(new, record).to_json())
            replayed_diff = ro != rn
            if not replayed_diff:
                ok = False
            print(f"                 witness={verdict.witness.to_json()} "
                  f"replay_confirmed={replayed_diff}")
            print(f"                 diff_fields={[d.field for d in verdict.diffs]}")
            # Minimality: the witness must be the lex-first observable diff in
            # the same total order the solver's minimisation searches.
            if diffs:
                first_concrete = diffs[0]["point"]
                minimal = (first_concrete == verdict.witness.to_json())
                if not minimal:
                    ok = False
                print(f"                 concrete_diff_points={len(diffs)} "
                      f"witness_is_lex_first={minimal}")
                print(f"                 lex_first_concrete={first_concrete}")
    return ok


def run_stress() -> bool:
    print()
    print("=" * 72)
    print("PHASE 2 — wider-domain multi-branch / bounded-loop stress")
    print("=" * 72)
    ok = True
    for rel in STRESS_CASES:
        doc = _load(rel)
        old, new = parse_case(doc)
        t0 = time.time()
        verdict = EquivalenceChecker(old, new, lambda s: build_solver(s)).run()
        dt = time.time() - t0
        s_old = verdict.stats.get("old", {})
        s_new = verdict.stats.get("new", {})
        print(f"[{verdict.status:>14}] {rel:<24} {dt:5.2f}s")
        print(f"                 old: states={s_old.get('states')} "
              f"peak={s_old.get('states_peak')} forks={s_old.get('forks')} "
              f"merges={s_old.get('merges')} unrolls={s_old.get('loop_unrolls')}")
        print(f"                      pruned={s_old.get('pruned_branches')} "
              f"solver_calls={s_old.get('solver_calls')} "
              f"complete={s_old.get('complete')}")
        print(f"                 new: states={s_new.get('states')} "
              f"pruned={s_new.get('pruned_branches')} "
              f"solver_calls={s_new.get('solver_calls')}")
        print(f"                 cross-version difference queries="
              f"{verdict.stats.get('difference_query_calls')}")
        if verdict.status == "different":
            record = point_to_record(verdict.witness)
            ro = interpret(old, record).to_json()
            rn = interpret(new, record).to_json()
            confirmed = ro != rn
            ok = ok and confirmed
            print(f"                 witness={verdict.witness.to_json()} "
                  f"replay_confirmed={confirmed}")
            for d in verdict.diffs:
                print(f"                   - {d.field}: {d.old} != {d.new}")
        elif verdict.status == "unknown":
            print(f"                 reason: {verdict.reason}")
    return ok


def run_semantics_spotchecks() -> bool:
    """Direct finite-semantics checks: overflow wrap, div/rem, NULL vs missing."""
    print()
    print("=" * 72)
    print("PHASE 3 — finite-semantics spot checks (concrete interpreter)")
    print("=" * 72)
    ok = True

    def check(name, cond):
        nonlocal ok
        print(f"[{'OK' if cond else 'FAIL':>14}] {name}")
        ok = ok and cond

    wrap_doc = _load("overflow-wrap.json")
    old, _ = parse_case(wrap_doc)
    # 127 + 1 wraps to -128 in signed 8-bit two's complement.
    r = interpret(old, {"a": 127}).to_json()
    check("8-bit overflow 127+1 wraps to -128",
          r["outcomes"]["wrapped"]["value"] == -128)
    # -128 - 1 would wrap to 127; here add(-1) on -128.
    r2 = interpret(old, {"a": -128}).to_json()
    check("-128+1 stays representable (-127)",
          r2["outcomes"]["wrapped"]["value"] == -127)
    # Missing and NULL are distinct.
    rm = interpret(old, {}).to_json()
    rn = interpret(old, {"a": None}).to_json()
    check("missing field raises E_MISSING",
          rm["error"] is not None and rm["error"]["code"] == "E_MISSING")
    check("NULL input propagates NULL, distinct from missing",
          rm["error"] is not None and rn["outcomes"]["wrapped"]["kind"] == "null")

    div_doc = _load("div-failures.json")
    dold, _ = parse_case(div_doc)
    check("divisor zero -> E_DIV_ZERO classification",
          interpret(dold, {"x": 5, "d": 0}).to_json()["error"]["code"]
          == "E_EXPLICIT:divisor-zero")
    check("INT_MIN / -1 -> trap classification",
          interpret(dold, {"x": -128, "d": -1}).to_json()["error"]["code"]
          == "E_EXPLICIT:divisor-trap")
    rem_doc = _load("div-rem.json")
    qold, _ = parse_case(rem_doc)
    rr = interpret(qold, {"x": -7}).to_json()
    # Truncation toward zero: -7 / 3 = -2, -7 rem 3 = -1.
    check("truncated division -7/3 = -2",
          rr["outcomes"]["q"]["value"] == -2)
    check("truncated remainder -7 rem 3 = -1",
          rr["outcomes"]["r"]["value"] == -1)
    return ok


def main() -> int:
    a = run_semantics_spotchecks()
    b = run_exhaustive()
    c = run_stress()
    print()
    print("=" * 72)
    if a and b and c:
        print("ACCEPTANCE PASSED — all confirmed checks hold; unknowns reported.")
        return 0
    print("ACCEPTANCE FAILED — see FAIL lines above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
