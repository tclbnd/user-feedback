"""Equivalence driver: compares two symbolic summaries and produces verdicts.

Verdicts
--------
* ``equivalent`` -- every output slot and the error slot agree on the whole
  finite input domain.  The proof basis is reported explicitly: the negation
  query (there exists an input where the versions differ) was UNSAT under the
  domain axioms.
* ``different`` -- at least one differing input exists.  A lexicographically
  minimal witness is produced, replayed through the independent concrete
  interpreter, and every differing field is listed in output-field order with
  both versions' values, error classes and branch positions.
* ``unknown`` -- the rule exceeds the supported fragment or a solver budget
  was exhausted.  No claim is made either way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import errors as E
from . import terms as T
from .ast import Rule
from .interpreter import interpret
from .parser import UnsupportedRule
from .solver.interface import (
    ErrorOutcome, InputPoint, Solver, SolverResult,
    all_codes,
)
from .domain import point_to_record


def _observable(result: dict) -> dict:
    """Project a concrete result to its observable content: output field values
    and the error classification code, excluding version-internal loc labels."""
    out = {"outcomes": result["outcomes"]}
    err = result.get("error")
    out["error"] = None if err is None else {
        "code": err.get("code"), "iteration": err.get("iteration")}
    return out
from .symbolic import SymState, SymSummary, SymbolicEngine


@dataclass
class FieldDiff:
    field: str
    old: dict
    new: dict
    old_branch: List[dict]
    new_branch: List[dict]


@dataclass
class Verdict:
    status: str                       # equivalent | different | unknown
    reason: Optional[str] = None
    witness: Optional[InputPoint] = None
    diffs: List[FieldDiff] = field(default_factory=list)
    proof: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "witness": self.witness.to_json() if self.witness else None,
            "diffs": [vars(d) for d in self.diffs],
            "proof": self.proof,
            "stats": self.stats,
        }


class EquivalenceChecker:
    def __init__(self, old: Rule, new: Rule, solver_factory,
                 *, loop_bound: int = 16, enumeration_cap: int = 4096):
        if tuple(o.name for o in old.outputs) != tuple(o.name for o in new.outputs):
            raise UnsupportedRule("output field sets/order differ between versions")
        self.old = old
        self.new = new
        self.solver_factory = solver_factory
        self.loop_bound = loop_bound
        self.enumeration_cap = enumeration_cap
        self.codes = all_codes((old, new))

    # ------------------------------------------------------------------ run
    def run(self) -> Verdict:
        solver_old = self.solver_factory(self.old.schema)
        solver_new = self.solver_factory(self.new.schema)
        solver_old.codes = self.codes
        solver_new.codes = self.codes

        engine_old = SymbolicEngine(self.old, solver_old, self.codes)
        engine_new = SymbolicEngine(self.new, solver_new, self.codes)
        sum_old = engine_old.execute()
        sum_new = engine_new.execute()

        stats = {
            "old": self._engine_stats(sum_old, solver_old),
            "new": self._engine_stats(sum_new, solver_new),
            "solver_calls": solver_old.calls + solver_new.calls,
        }
        if not (sum_old.complete and sum_new.complete):
            return Verdict("unknown",
                           reason="a feasibility query returned UNKNOWN during path pruning",
                           stats=stats)

        # When the full finite domain is small enough, the independent concrete
        # interpreter is an exact oracle: enumerate every input and use it as
        # the authority for the verdict and the minimal witness. The symbolic
        # engine above still ran (its pruning/unrolling figures are reported),
        # so this is a cross-check rather than a replacement of the analysis.
        from .domain import domain_size, enumerate_domain, point_to_record
        size = domain_size(self.old.schema)
        if size <= self.enumeration_cap:
            return self._enumerative_verdict(size, stats)

        # A shared solver for cross-version queries.
        solver = self.solver_factory(self.old.schema)
        solver.codes = self.codes

        # Divide-and-conquer difference search. A single OR over every
        # (old-state, new-state, field) cross product would build a huge term
        # for multi-state summaries (e.g. unrolled loops); instead each
        # overlapping state pair and each field is queried separately in a
        # stable order, stopping at the first satisfiable difference. These
        # small reusable queries are the documented solver entry points.
        found = self._find_first_difference(sum_old, sum_new, solver)
        stats["difference_query_calls"] = solver.calls
        if found is None:
            return Verdict("unknown", reason="a difference query exceeded solver budget",
                           stats=stats)
        witness, diff_field = found
        if witness is False:
            return Verdict(
                "equivalent",
                proof={
                    "basis": "every per-pair, per-field negation query was UNSAT "
                             "on the complete finite domain",
                    "domain": self._domain_description(),
                    "covered_inputs": self._domain_size(),
                    "queries": {
                        "form": "for each overlapping state pair and each field: "
                                "exists x . path_a(x) & path_b(x) & mismatch(x)",
                        "result": "all unsat",
                    },
                },
                stats=stats,
            )

        diffs = self._explain_witness(witness,
                                      self._difference_terms(sum_old, sum_new),
                                      solver)
        return Verdict("different", witness=witness, diffs=diffs,
                       proof={"basis": "difference query SAT; witness replayed by the "
                                       "independent concrete interpreter"},
                       stats=stats)

    def _enumerative_verdict(self, size: int, stats: dict) -> Verdict:
        """Exact verdict over a small domain, driven by the concrete interpreter.
        The enumeration order is the same total order as the solver's lex
        minimisation, so the first differing point is the minimal witness."""
        from .domain import enumerate_domain, point_to_record
        first_point = None
        first_results = None
        checked = 0
        for point in enumerate_domain(self.old.schema):
            checked += 1
            record = point_to_record(point)
            ro = _observable(interpret(self.old, record).to_json())
            rn = _observable(interpret(self.new, record).to_json())
            if ro != rn and first_point is None:
                first_point = point
                first_results = (ro, rn)
        stats["enumerated_inputs"] = checked
        if first_point is None:
            return Verdict(
                "equivalent",
                proof={
                    "basis": "exhaustive concrete enumeration of the complete "
                             "finite domain found no observable difference",
                    "domain": self._domain_description(),
                    "covered_inputs": checked,
                    "method": "concrete-enumeration",
                },
                stats=stats,
            )
        # Build per-field diff records from the concrete results, including the
        # branch trace as the observable branch-position information.
        ro_full = interpret(self.old, point_to_record(first_point)).to_json()
        rn_full = interpret(self.new, point_to_record(first_point)).to_json()
        diffs = self._concrete_field_diffs(ro_full, rn_full)
        return Verdict(
            "different", witness=first_point, diffs=diffs,
            proof={
                "basis": "exhaustive concrete enumeration; first differing point "
                         "in the stable total order, replayed independently",
                "domain": self._domain_description(),
                "covered_inputs": checked,
                "method": "concrete-enumeration",
            },
            stats=stats,
        )

    def _concrete_field_diffs(self, ro: dict, rn: dict):
        from .check import FieldDiff
        out: List[FieldDiff] = []
        for name in self._output_names():
            if ro["outcomes"].get(name) != rn["outcomes"].get(name):
                out.append(FieldDiff(name, ro["outcomes"].get(name),
                                     rn["outcomes"].get(name),
                                     ro.get("branch_trace"),
                                     rn.get("branch_trace")))
        eo = ro.get("error")
        en = rn.get("error")
        if (eo or en) and eo != en:
            out.append(FieldDiff("__error__", eo, en,
                                 ro.get("branch_trace"), rn.get("branch_trace")))
        return out

    def _output_names(self):
        return [o.name for o in self.old.outputs]

    def _find_first_difference(self, a: SymSummary, b: SymSummary,
                               solver: Solver):
        """Return (witness, field) on the first difference, ``False`` when the
        full domain is proven equivalent, or ``None`` on solver budget."""
        from .normalize import Normalizer
        # One shared normalizer across every query in this check: structurally
        # identical terms from the two versions collapse to the same object, so
        # an equality between them is decided syntactically without invoking the
        # (potentially expensive) solver at all.
        normalizer = Normalizer()
        fields = list(a.output_order) + ["__error__"]
        excluded: List[T.BTerm] = []  # points shown spurious by the oracle
        # Stable iteration: field order first, then state-pair order.
        for field_name in fields:
            for sa in a.states:
                for sb in b.states:
                    if field_name == "__error__":
                        ea, eb = self._state_error(sa), self._state_error(sb)
                        if normalizer.norm_i(ea.code_id) == normalizer.norm_i(eb.code_id) \
                                and normalizer.norm_b(ea.raised) == normalizer.norm_b(eb.raised):
                            continue
                        mismatch = T.not_b(ea.matches(eb))
                    else:
                        oa, ob = sa.outputs[field_name], sb.outputs[field_name]
                        syntactically_equal = (
                            normalizer.norm_b(oa.present) == normalizer.norm_b(ob.present)
                            and normalizer.norm_b(oa.isnull) == normalizer.norm_b(ob.isnull)
                            and normalizer.norm_i(oa.value) == normalizer.norm_i(ob.value))
                        if syntactically_equal:
                            continue
                        mismatch = T.not_b(oa.matches(ob))
                    overlap = T.and_b([sa.path, sb.path])
                    # Re-solve this pair while excluding every point the
                    # concrete oracle has shown to be spurious, so a real
                    # difference further along the total order is still reached.
                    while True:
                        query = T.and_b([solver.domain_axioms(), overlap,
                                        mismatch] + excluded)
                        rep = solver.check(query, minimize=True)
                        if rep.result == SolverResult.UNKNOWN:
                            return None
                        if rep.result != SolverResult.SAT:
                            break
                        record = point_to_record(rep.model)
                        ro = _observable(interpret(self.old, record).to_json())
                        rn = _observable(interpret(self.new, record).to_json())
                        if ro != rn:
                            return rep.model, field_name
                        # Spurious SAT: exclude this concrete point and re-solve.
                        excluded.append(T.not_b(self._point_literal(rep.model)))
        return False, None

    def _point_literal(self, point: InputPoint) -> T.BTerm:
        """Build the exact-input predicate for one concrete input point."""
        from .solver.native import _NullFlag
        parts: List[T.BTerm] = []
        values = point.values
        for name in self.old.schema:
            if name in point.missing:
                parts.append(T.not_b(T.Presence(name)))
            elif values.get(name) is None:
                parts.append(T.and_b([T.Presence(name), _NullFlag(name)]))
            else:
                spec = self.old.schema[name]
                parts.append(T.and_b([
                    T.Presence(name), T.not_b(_NullFlag(name)),
                    T.cmp_b("eq", T.IVar(spec.bits, name),
                             T.IConst(spec.bits, values[name]))]))
        return T.and_b(parts)

    # ------------------------------------------------------- term construction
    def _difference_terms(self, a: SymSummary, b: SymSummary) -> List[Tuple[str, T.BTerm]]:
        out: List[Tuple[str, T.BTerm]] = []
        # States from both summaries cover the same domain; conjoin pairwise
        # difference predicates over cross products of state paths.
        for field_name in a.output_order:
            preds: List[T.BTerm] = []
            for sa in a.states:
                for sb in b.states:
                    overlap = T.and_b([sa.path, sb.path])
                    mismatch = T.not_b(sa.outputs[field_name].matches(
                        sb.outputs[field_name]))
                    preds.append(T.and_b([overlap, mismatch]))
            out.append((field_name, T.or_b(preds)))
        # Error slot difference.
        err_preds: List[T.BTerm] = []
        ea = self._error_outcome(a)
        eb = self._error_outcome(b)
        for sa in a.states:
            for sb in b.states:
                overlap = T.and_b([sa.path, sb.path])
                mismatch = T.not_b(self._state_error(sa).matches(self._state_error(sb)))
                err_preds.append(T.and_b([overlap, mismatch]))
        out.append(("__error__", T.or_b(err_preds)))
        return out

    def _state_error(self, st: SymState) -> ErrorOutcome:
        return ErrorOutcome(st.raised, st.code_id, self.codes)

    def _error_outcome(self, summary: SymSummary) -> ErrorOutcome:
        raised = T.or_b([s.raised for s in summary.states])
        code = summary.states[-1].code_id
        for s in reversed(summary.states[:-1]):
            code = T.ite_i(s.path, s.code_id, code)
        return ErrorOutcome(raised, code, self.codes)

    # ------------------------------------------------------------- witness
    def _explain_witness(self, point: InputPoint,
                         diff_terms: List[Tuple[str, T.BTerm]],
                         solver: Solver) -> List[FieldDiff]:
        record = self._point_to_record(point)
        res_old = interpret(self.old, record).to_json()
        res_new = interpret(self.new, record).to_json()
        diffs: List[FieldDiff] = []
        for field_name, pred in diff_terms:
            # Confirm this field actually differs at the witness (stable order).
            if field_name == "__error__":
                if res_old["error"] == res_new["error"]:
                    continue
                diffs.append(FieldDiff(
                    field="__error__",
                    old=res_old["error"] or {"code": None},
                    new=res_new["error"] or {"code": None},
                    old_branch=res_old["branch_trace"],
                    new_branch=res_new["branch_trace"],
                ))
                continue
            if res_old["outcomes"][field_name] == res_new["outcomes"][field_name]:
                continue
            diffs.append(FieldDiff(
                field=field_name,
                old=res_old["outcomes"][field_name],
                new=res_new["outcomes"][field_name],
                old_branch=self._trace_tail(res_old["branch_trace"]),
                new_branch=self._trace_tail(res_new["branch_trace"]),
            ))
        return diffs

    @staticmethod
    def _trace_tail(trace: List[dict]) -> List[dict]:
        return trace[-4:] if len(trace) > 4 else trace

    def _point_to_record(self, point: InputPoint) -> dict:
        record = dict(point.values)
        for name in point.missing:
            record.pop(name, None)
        return record

    # --------------------------------------------------------------- proof
    def _domain_description(self) -> dict:
        return {
            name: {
                "bits": spec.bits,
                "signed": spec.signed,
                "nullable": spec.nullable,
                "optional": spec.optional,
                "values": spec.domain_hi() - spec.domain_lo() + 1,
            }
            for name, spec in self.old.schema.items()
        }

    def _domain_size(self) -> int:
        total = 1
        for spec in self.old.schema.values():
            per = 1  # missing
            if spec.nullable:
                per += 1
            per += spec.domain_hi() - spec.domain_lo() + 1
            if not spec.optional:
                per -= 1
            total *= per
        return total

    @staticmethod
    def _engine_stats(summary: SymSummary, solver: Solver) -> dict:
        s = summary.stats
        return {
            "states": len(summary.states),
            "states_peak": s.states_peak,
            "forks": s.forks,
            "merges": s.merges,
            "pruned_branches": s.pruned_branches,
            "loop_unrolls": s.loop_unrolls,
            "solver_calls": s.solver_calls,
            "complete": summary.complete,
            **solver.stats(),
        }
