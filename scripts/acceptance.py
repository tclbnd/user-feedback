"""One-command local acceptance suite.

Run with::

    python3 -I scripts/acceptance.py

Stage A -- small-domain exhaustive cross-check.  Every small synthetic case
is enumerated completely with the concrete interpreter; the symbolic verdict
and its minimal witness must match the brute-force oracle exactly, and each
witness is replayed independently.

Stage B -- wide-domain branch stress.  A 12-bit multi-layer branch case is
checked symbolically only (full enumeration would be ~16 million rows) and
the report prints live states, pruned paths, merges and solver calls.

The process exits non-zero on any failed assertion.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl_eq import cases, parse_case
from etl_eq.concrete import run_rule
from etl_eq.equivalence import (
    EQUIVALENT,
    NOT_EQUIVALENT,
    UNDECIDABLE,
    check_equivalence,
)
from etl_eq.exhaustive import brute_force_compare
from etl_eq.semantics import MISSING, NULL, RuleFailure, outcomes_equal

PASS = "PASS"
FAIL = "FAIL"

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def stage_a():
    print("=" * 72)
    print("STAGE A: small-domain exhaustive cross-check")
    print("=" * 72)
    builders = [
        ("overflow_edge", cases.overflow_edge_case, 4),
        ("null_branch", cases.null_branch_case, 4),
        ("loop_last_iter", cases.loop_last_iter_case, 4),
        ("div_zero", cases.div_zero_case, 4),
        ("equivalent_arith", cases.equivalent_arith_case, 4),
        ("equivalent_branch", cases.equivalent_branch_case, 4),
        ("equivalent_loop", cases.equivalent_loop_case, 4),
    ]
    for name, builder, bits in builders:
        doc = builder(bits) if "bits" in builder.__code__.co_varnames else builder()
        old, new = parse_case(doc)
        t0 = time.time()
        report = check_equivalence(old, new)
        sym_ms = (time.time() - t0) * 1000
        oracle = brute_force_compare(old, new)

        expect_equiv = doc["_expect"]["kind"] == "equivalent"
        verdict_ok = (report.verdict == EQUIVALENT) == expect_equiv
        if report.verdict == UNDECIDABLE:
            verdict_ok = False

        # oracle agreement
        if expect_equiv:
            oracle_ok = oracle["differing_count"] == 0
        else:
            oracle_ok = (
                oracle["differing_count"] > 0
                and report.verdict == NOT_EQUIVALENT
                and report.witnesses
                and report.witnesses[0].record == oracle["minimal_record"]
            )

        # independent concrete replay of every witness
        replay_ok = True
        for w in report.witnesses:
            replay_ok &= outcomes_equal(run_rule(old, w.record), w.old)
            replay_ok &= outcomes_equal(run_rule(new, w.record), w.new)
            # and the replay must actually differ when a difference is claimed
            if not expect_equiv:
                replay_ok &= not outcomes_equal(w.old, w.new)

        # expected trigger coverage: the designed trigger row must be a real
        # differing row (the minimal witness is separately oracle-checked)
        trigger_ok = True
        if "trigger" in doc["_expect"]:
            trigger = doc["_expect"]["trigger"]
            diff_records = oracle["all_records"]
            trigger_ok = any(
                all(r.get(k) == v or (v is None and r.get(k) is None)
                    for k, v in trigger.items())
                for r in diff_records
            )

        exec_stats = report.old_execution
        detail = (
            f"domain={oracle['domain_size']} diffs={oracle['differing_count']} "
            f"verdict={report.verdict} pairs={report.feasible_pairs}/"
            f"{report.feasible_pairs + report.infeasible_pairs} "
            f"pruned={exec_stats.pruned_branches} merged={exec_stats.merged_states} "
            f"calls={exec_stats.solver_calls + report.new_execution.solver_calls} "
            f"{sym_ms:.0f}ms"
        )
        record(f"A.{name}", verdict_ok and oracle_ok and replay_ok and trigger_ok, detail)

        if report.witnesses:
            w = report.witnesses[0]
            print(f"        minimal witness: {_fmt_record(w.record)}")
            print(f"        old: {w.old.as_jsonable()} @ {w.old_locations}")
            print(f"        new: {w.new.as_jsonable()} @ {w.new_locations}")
            for d in w.differences:
                print(f"        diff: {d}")


def stage_b():
    print("=" * 72)
    print("STAGE B: wide-domain multi-layer branch stress (12-bit)")
    print("=" * 72)
    for bits in (8, 12):
        doc = cases.branch_stress_case(bits)
        old, new = parse_case(doc)
        domain = check_equivalence(old, new).domain_size
        t0 = time.time()
        report = check_equivalence(old, new, budget=500_000)
        ms = (time.time() - t0) * 1000
        if report.verdict == UNDECIDABLE:
            record(f"B.stress i{bits}", False,
                   f"UNDECIDABLE: {report.undecidable_reason}")
            continue
        oe, ne = report.old_execution, report.new_execution
        detail = (
            f"domain={domain} verdict={report.verdict} "
            f"live_states(old/new)={len(oe.states)}/{len(ne.states)} "
            f"max_live={max(oe.max_live_states, ne.max_live_states)} "
            f"pruned={oe.pruned_branches + ne.pruned_branches} "
            f"merged={oe.merged_states + ne.merged_states} "
            f"solver_calls={oe.solver_calls + ne.solver_calls} "
            f"pairs={report.feasible_pairs}/{report.feasible_pairs + report.infeasible_pairs} "
            f"{ms:.0f}ms"
        )
        ok = report.verdict == NOT_EQUIVALENT and bool(report.witnesses)
        record(f"B.stress i{bits}", ok, detail)
        if report.witnesses:
            w = report.witnesses[0]
            print(f"        minimal witness: {_fmt_record(w.record)}")
            print(f"        old: {w.old.error} @ {w.old_locations}")
            print(f"        new: {w.new.error} @ {w.new_locations}")

    # wide overflow boundary case: prove equality except at the exact edge
    print("-" * 72)
    doc = cases.overflow_edge_case(12)
    old, new = parse_case(doc)
    report = check_equivalence(old, new, budget=500_000)
    oe = report.old_execution
    ok = report.verdict == NOT_EQUIVALENT and report.witnesses and \
        report.witnesses[0].record["a"] == 2 ** 11 - 1
    record("B.overflow-edge i12", ok,
           f"verdict={report.verdict} witness_a="
           f"{report.witnesses[0].record.get('a') if report.witnesses else '-'} "
           f"pruned={oe.pruned_branches} calls={oe.solver_calls + report.new_execution.solver_calls}")


def stage_d_merge_budget():
    """Confluence merging, feasible-path pruning and budget honesty."""
    print("=" * 72)
    print("STAGE D: state merging, pruning and undecidable handling")
    print("=" * 72)
    from etl_eq.semantics import IntType
    from etl_eq.symbolic import SymbolicEngine, PathState, Sym
    from etl_eq.rules import FieldSpec, Schema, Rule
    from etl_eq.fdsolver import FiniteDomainSolver

    # D1. compatibility-driven merge at a confluence
    i4 = IntType(4, True)
    rule = Rule(Schema((FieldSpec("a", i4, False, False),)), ())
    eng = SymbolicEngine(rule, FiniteDomainSolver, 200000)
    dom = ("__domain__", "a", tuple(range(i4.min, i4.max + 1)))
    v = Sym(("v", "a", i4), "int", i4)
    out_sym = Sym(("c", 1), "int")
    s1 = PathState(
        constraints=[dom, ("true", ("cmp", "ge", ("v", "a", i4), ("c", 0)))],
        env={"a": v}, emitted=(("z", out_sym),), locations=("L1",))
    s2 = PathState(
        constraints=[dom, ("true", ("cmp", "le", ("v", "a", i4), ("c", 2)))],
        env={"a": v}, emitted=(("z", out_sym),), locations=("L2",))
    merged = eng._merge([s1, s2])
    record("D1.compatible-confluence-merge",
           len(merged) == 1 and eng.merged == 1 and
           set(merged[0].locations) == {"L1", "L2"},
           f"merged_states={eng.merged}")

    # D2. contradictory states must never be merged
    eng2 = SymbolicEngine(rule, FiniteDomainSolver, 200000)
    s3 = PathState(
        constraints=[dom, ("true", ("cmp", "gt", ("v", "a", i4), ("c", 0)))],
        env={"a": v}, emitted=(("z", out_sym),), locations=("L3",))
    s4 = PathState(
        constraints=[dom, ("false", ("cmp", "gt", ("v", "a", i4), ("c", 0)))],
        env={"a": v}, emitted=(("z", out_sym),), locations=("L4",))
    separate = eng2._merge([s3, s4])
    record("D2.contradictory-states-separate",
           len(separate) == 2 and eng2.merged == 0,
           f"kept_states={len(separate)}")

    # D3. feasible-path pruning on a rule with a dead guard, oracle-checked
    doc = cases.merge_confluence_case(4)
    old, new = parse_case(doc)
    rep = check_equivalence(old, new)
    oracle = brute_force_compare(old, new)
    record("D3.dead-guard-pruning",
           rep.verdict == EQUIVALENT and
           rep.old_execution.pruned_branches > 0 and
           oracle["differing_count"] == 0,
           f"pruned={rep.old_execution.pruned_branches} "
           f"max_live={rep.old_execution.max_live_states}")

    # D4. exhausted search budget must surface UNDECIDABLE, never a guess
    doc2 = cases.undecidable_case(16)
    old2, new2 = parse_case(doc2)
    rep2 = check_equivalence(old2, new2, budget=1)
    record("D4.budget-exhaustion-undecidable",
           rep2.verdict == UNDECIDABLE and rep2.undecidable_reason is not None,
           str(rep2.undecidable_reason))

    # D5. MISSING vs NULL: absent key == MISSING marker, Python None == NULL,
    # and symbolic verdict agrees with the brute-force oracle.
    from etl_eq.semantics import MISSING as M, NULL as N
    null_doc = {
        "input": {"x": {"bits": 4, "signed": True,
                        "nullable": True, "optional": True}},
        "old": {"output": {"y": {
            "if": [{"is_missing": "x"}, {"integer": 0},
                   {"if": [{"is_null": "x"}, {"integer": 0},
                           {"add": [{"field": "x"}, {"integer": 1}],
                            "bits": 4}]}]}}},
        "new": {"output": {"y": {
            "coalesce": [
                {"add": [{"field": "x"}, {"integer": 1}], "bits": 4},
                {"integer": 0}]}}},
    }
    o_rule, n_rule = parse_case(null_doc)
    marker_ok = outcomes_equal(run_rule(o_rule, {}), run_rule(o_rule, {"x": M}))
    marker_ok &= outcomes_equal(run_rule(o_rule, {"x": None}),
                                run_rule(o_rule, {"x": N}))
    null_rep = check_equivalence(o_rule, n_rule)
    null_oracle = brute_force_compare(o_rule, n_rule)
    record("D5.missing-null-distinction",
           marker_ok and null_rep.verdict == EQUIVALENT and
           null_oracle["differing_count"] == 0,
           f"domain={null_oracle['domain_size']}")


def stage_c_semantics():
    """Direct finite-semantics unit checks against the concrete oracle."""
    print("=" * 72)
    print("STAGE C: finite semantics unit checks")
    print("=" * 72)
    from etl_eq.semantics import IntType
    from etl_eq.concrete import ConcreteInterpreter
    from etl_eq.rules import Rule, Schema, FieldSpec

    i8 = IntType(8, True, "wrap")
    u8 = IntType(8, False, "wrap")
    checks = [
        ("wrap signed 127+1", i8.apply_overflow(127 + 1) == -128),
        ("wrap unsigned 255+1", u8.apply_overflow(255 + 1) == 0),
        ("saturate top", IntType(8, True, "saturate").apply_overflow(200) == 127),
        ("overflow policy raises", _raises(lambda: IntType(8, True, "error").apply_overflow(200))),
        ("trunc div -7/2", (-7 // 2) == -4 and int(-7 / 2) == -3),
        ("markers distinct", MISSING is not NULL and MISSING != NULL),
    ]
    for name, ok in checks:
        record(f"C.{name}", ok)

    # div-by-zero classification through the interpreter
    schema = Schema((FieldSpec("a", i8, False, False), FieldSpec("b", i8, False, False)))
    div_expr = {"div": [{"field": "a"}, {"field": "b"}], "bits": 8}
    case_doc = {"input": {"a": {"bits": 8}, "b": {"bits": 8}},
                "old": {"statements": [{"emit": "q", "expr": div_expr}]},
                "new": {"statements": [{"emit": "q", "expr": dict(div_expr)}]}}
    rule = parse_case(case_doc)[0]
    out = ConcreteInterpreter(rule).run({"a": 5, "b": 0})
    record("C.div-by-zero classified", out.error == "div-by-zero", str(out.error_location))
    out2 = ConcreteInterpreter(rule).run({"a": 6, "b": 2})
    record("C.ordinary division", out2.error is None and dict(out2.fields)["q"] == 3)


def _raises(fn):
    try:
        fn()
    except RuleFailure:
        return True
    return False


def _fmt_record(record):
    from etl_eq.semantics import MISSING, NULL
    parts = []
    for k in sorted(record):
        v = record[k]
        parts.append(f"{k}={'MISSING' if v is MISSING else 'NULL' if v is NULL else v}")
    return "{" + ", ".join(parts) + "}"


def main():
    stage_c_semantics()
    stage_a()
    stage_b()
    stage_d_merge_budget()
    print("=" * 72)
    total = len(results)
    failed = [r for r in results if not r[1]]
    print(f"SUMMARY: {total - len(failed)}/{total} checks passed")
    if failed:
        for name, _, detail in failed:
            print(f"  FAILED: {name} {detail}")
        sys.exit(1)
    print("All acceptance checks passed.")


if __name__ == "__main__":
    main()
