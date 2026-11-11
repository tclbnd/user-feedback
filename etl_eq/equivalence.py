"""Equivalence checking entry point.

The checker pairs the terminal states of the two symbolic executions.  Each
version's states partition the (finite) input domain exactly: every record
satisfies the path constraint of precisely one live state, because every
branch fork is exhaustive (condition True vs. not-True) and every failure
guard is exhaustive (fires vs. does not fire).  Therefore proving that *every
feasible old/new state pair is observationally equal* is a proof over the
complete input domain -- not a sampling argument.

Each pair needs at most two reusable solver queries:

1. feasibility of the union of the two path constraints (is there any input
   reaching both states?);
2. a difference query under that union (error classification mismatch,
   output-field set mismatch, or ``neval`` on a field value).

When the difference query is satisfiable its lexicographically smallest
model becomes a candidate minimal witness; the globally smallest candidate
is reported.  Every witness is independently replayed through the concrete
interpreter before it is handed out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import fdsolver
from .fdsolver import BudgetExceeded, Domain, FiniteDomainSolver
from .concrete import run_rule
from .rules import Rule
from .semantics import (
    MISSING,
    NULL,
    Outcome,
    UnsupportedRule,
    value_order_key,
)
from .symbolic import ExecutionReport, PathState, SymbolicEngine

EQUIVALENT = "EQUIVALENT"
NOT_EQUIVALENT = "NOT_EQUIVALENT"
UNDECIDABLE = "UNDECIDABLE"


@dataclass
class Witness:
    record: Dict[str, Any]
    field_order: Tuple[str, ...]
    old: Outcome
    new: Outcome
    old_locations: Tuple[str, ...]
    new_locations: Tuple[str, ...]
    differences: List[dict]


@dataclass
class EquivalenceReport:
    verdict: str
    witnesses: List[Witness] = field(default_factory=list)
    old_execution: Optional[ExecutionReport] = None
    new_execution: Optional[ExecutionReport] = None
    feasible_pairs: int = 0
    infeasible_pairs: int = 0
    domain_size: int = 0
    undecidable_reason: Optional[str] = None
    solver_budget: int = 0


class EquivalenceChecker:
    def __init__(self, old: Rule, new: Rule, budget: int = 200_000,
                 solver_factory=FiniteDomainSolver):
        self.old = old
        self.new = new
        self.budget = budget
        self._solver_factory = solver_factory
        self.field_order = old.schema.names
        self.domain_size = self._domain_size()

    def _domain_size(self) -> int:
        total = 1
        for spec in self.old.schema.fields:
            total *= (spec.type.max - spec.type.min + 1) + len(spec.domain_markers())
        return total

    def _new_solver(self, constraints_old, constraints_new) -> FiniteDomainSolver:
        solver = self._solver_factory(budget=self.budget)
        domains: Dict[str, Domain] = {}
        for con in list(constraints_old) + list(constraints_new):
            if con[0] == "__domain__":
                domains[con[1]] = Domain(con[2])
        for spec in self.old.schema.fields:
            domains.setdefault(spec.name, Domain.int_range(spec.type))
        for name, dom in domains.items():
            solver.add_var(name, dom, "int")
        for con in list(constraints_old) + list(constraints_new):
            if con[0] != "__domain__":
                solver.add_constraint(con[0], *con[1:])
        return solver

    def check(self) -> EquivalenceReport:
        try:
            old_engine = SymbolicEngine(self.old, self._solver_factory, self.budget)
            new_engine = SymbolicEngine(self.new, self._solver_factory, self.budget)
            old_exec = old_engine.execute()
            new_exec = new_engine.execute()
        except BudgetExceeded as exc:
            return EquivalenceReport(
                UNDECIDABLE, undecidable_reason=f"solver budget exhausted: {exc}",
                solver_budget=self.budget,
            )
        except UnsupportedRule as exc:
            return EquivalenceReport(
                UNDECIDABLE, undecidable_reason=f"unsupported construct: {exc}",
                solver_budget=self.budget,
            )

        report = EquivalenceReport(
            EQUIVALENT,
            old_execution=old_exec,
            new_execution=new_exec,
            domain_size=self.domain_size,
            solver_budget=self.budget,
        )
        best: Optional[Tuple[tuple, Witness]] = None
        try:
            for o_state in old_exec.states:
                for n_state in new_exec.states:
                    solver = self._new_solver(o_state.constraints, n_state.constraints)
                    if not solver.feasible():
                        report.infeasible_pairs += 1
                        continue
                    report.feasible_pairs += 1
                    witness = self._difference(o_state, n_state, solver)
                    if witness is not None:
                        report.verdict = NOT_EQUIVALENT
                        key = tuple(value_order_key(witness.record[f]) for f in self.field_order)
                        if best is None or key < best[0]:
                            best = (key, witness)
        except BudgetExceeded as exc:
            return EquivalenceReport(
                UNDECIDABLE,
                old_execution=old_exec,
                new_execution=new_exec,
                feasible_pairs=report.feasible_pairs,
                infeasible_pairs=report.infeasible_pairs,
                domain_size=self.domain_size,
                undecidable_reason=f"solver budget exhausted: {exc}",
                solver_budget=self.budget,
            )

        if best is not None:
            report.witnesses = [best[1]]
        return report

    # -- difference queries ----------------------------------------------
    def _difference(self, o: PathState, n: PathState, solver: FiniteDomainSolver):
        # 1. error classification mismatch
        if (o.error is None) != (n.error is None):
            model = solver.find_minimal_model(list(self.field_order))
            return self._build_witness(model, o, n)
        if o.error is not None and n.error is not None:
            if o.error[0] != n.error[0]:
                model = solver.find_minimal_model(list(self.field_order))
                return self._build_witness(model, o, n)
            return None  # same classification on every row of this pair

        # 2. output field set mismatch
        o_names = tuple(name for name, _ in o.emitted)
        n_names = tuple(name for name, _ in n.emitted)
        if o_names != n_names:
            model = solver.find_minimal_model(list(self.field_order))
            return self._build_witness(model, o, n)

        # 3. per-field value difference, field order determines witness order
        for name, o_sym in o.emitted:
            n_sym = dict(n.emitted)[name]
            diff_solver = self._extend(solver, ("neval", o_sym.node, n_sym.node))
            model = diff_solver.find_minimal_model(list(self.field_order))
            if model is not None:
                return self._build_witness(model, o, n)
        return None

    def _extend(self, solver: FiniteDomainSolver, constraint: tuple) -> FiniteDomainSolver:
        clone = self._solver_factory(budget=self.budget)
        for name, dom in solver.domains.items():
            clone.add_var(name, dom, solver.kinds[name])
        for con in solver.constraints:
            clone.add_constraint(con.kind, *con.nodes)
        clone.add_constraint(constraint[0], *constraint[1:])
        return clone

    # -- witness construction with independent concrete replay -----------
    def _build_witness(self, model: Dict[str, Any], o: PathState, n: PathState) -> Witness:
        if model is None:
            raise BudgetExceeded("no minimal model within budget")
        record = {f: model[f] for f in self.field_order if f in model}
        # Independent replay through the concrete interpreter is the source
        # of truth for both values and the precise failing branch location.
        old_outcome = run_rule(self.old, record)
        new_outcome = run_rule(self.new, record)
        diffs = self._describe_diffs(record, model, o, n)
        old_locs = tuple(o.locations) + \
            ((old_outcome.error_location,) if old_outcome.error_location and
             old_outcome.error_location not in o.locations else ())
        new_locs = tuple(n.locations) + \
            ((new_outcome.error_location,) if new_outcome.error_location and
             new_outcome.error_location not in n.locations else ())
        return Witness(
            record=record,
            field_order=self.field_order,
            old=old_outcome,
            new=new_outcome,
            old_locations=old_locs,
            new_locations=new_locs,
            differences=diffs,
        )

    def _describe_diffs(self, record, model, o: PathState, n: PathState) -> List[dict]:
        diffs: List[dict] = []
        if (o.error is None) != (n.error is None) or \
                (o.error and n.error and o.error[0] != n.error[0]):
            diffs.append({
                "kind": "error-classification",
                "old_error": o.error[0] if o.error else None,
                "new_error": n.error[0] if n.error else None,
            })
        o_map = dict(o.emitted)
        n_map = dict(n.emitted)
        output_names = tuple(name for name, _ in o.emitted)
        for name in output_names:
            if name not in o_map or name not in n_map:
                diffs.append({
                    "kind": "field-presence",
                    "field": name,
                    "old_present": name in o_map,
                    "new_present": name in n_map,
                })
                continue
            ov = fdsolver.evaluate(o_map[name].node, model)
            nv = fdsolver.evaluate(n_map[name].node, model)
            if not fdsolver.values_equal(ov, nv):
                if isinstance(ov, tuple) and ov and ov[0] == "ERROR" or \
                        isinstance(nv, tuple) and nv and nv[0] == "ERROR":
                    diffs.append({
                        "kind": "error-classification",
                        "field": name,
                        "old_value": _display(ov),
                        "new_value": _display(nv),
                    })
                else:
                    diffs.append({
                        "kind": "field-value",
                        "field": name,
                        "old_value": _display(ov),
                        "new_value": _display(nv),
                    })
        return diffs


def _display(v: Any):
    if v is MISSING:
        return "MISSING"
    if v is NULL:
        return "NULL"
    if isinstance(v, tuple) and v and v[0] == "ERROR":
        return f"ERROR:{v[1]}"
    return v


def check_equivalence(old: Rule, new: Rule, budget: int = 200_000) -> EquivalenceReport:
    return EquivalenceChecker(old, new, budget=budget).check()
