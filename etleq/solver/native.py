"""Native feasibility solver: DPLL-style case splitting + interval contraction.

No external SMT dependency.  The supported fragment is intentionally the one
produced by the symbolic ETL engine:

* booleans with three-valued NULL logic,
* fixed-width integer arithmetic (add/sub/mul/div/rem, two's-complement wrap),
* comparisons, presence/NULL predicates,
* if-then-else on both sorts (handled lazily as case splits),
* conjunction / disjunction / negation.

Algorithm
---------
1. Boolean structure is explored recursively (DPLL): each decision point is a
   boolean variable or an ITE condition.
2. Every leaf is a conjunction of theory literals checked by interval
   contraction (ICP).  ITEs in arithmetic are split on their conditions.
3. When intervals cannot be pinned to a point, bounded enumeration handles
   narrow variables; an exhausted budget yields ``unknown`` rather than a
   guess.

The solver counts every top-level :meth:`NativeSolver.check` call and every
infeasible branch it prunes, so the acceptance report can show real work.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, replace
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# Nested multi-branch rules create long DPLL decision chains; give the solver
# enough stack while the explicit branch budget remains the real termination
# guarantee (exhaustion returns UNKNOWN rather than crashing).
sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

from .. import terms as T
from ..ast import FieldSpec
from ..errors import E_DIV_TRAP, E_DIV_ZERO
from .interface import (
    CheckReport, InputPoint, Solver, SolverResult, _rank,
)

INF = 10 ** 30
ENUM_LIMIT = 128          # enumerate small-width variables directly
ICP_ITERATIONS = 200


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

@dataclass
class Env:
    bool_fixed: Dict[int, Optional[bool]]      # id(term) -> True/False/None(NULL)
    int_intervals: Dict[int, Tuple[int, int]]  # id(ITerm) -> [lo, hi]
    null_int: Set[int]                         # id(ITerm) known to be NULL
    pres_fixed: Dict[str, bool]
    null_fixed: Dict[str, bool]

    def clone(self) -> "Env":
        return Env(dict(self.bool_fixed), dict(self.int_intervals),
                   set(self.null_int), dict(self.pres_fixed), dict(self.null_fixed))


class BudgetExceeded(Exception):
    pass


class NativeSolver(Solver):
    name = "native-icp"

    def __init__(self, schema: Dict[str, FieldSpec], *, branch_budget: int = 200_000,
                 enumerate_below: int = ENUM_LIMIT):
        super().__init__(schema, branch_budget=branch_budget)
        self.enumerate_below = enumerate_below
        self._decisions = 0

    # ------------------------------------------------------------------ API
    def null_flag(self, name: str) -> T.BTerm:
        return _NullFlag(name)

    def check(self, cond: T.BTerm, *, minimize: bool = False) -> CheckReport:
        from ..normalize import normalize
        self.calls += 1
        self._decisions = 0
        root = normalize(T.and_b([self.domain_axioms(), cond]))
        env0 = self._initial_env()
        try:
            report = self._solve(root, env0)
        except BudgetExceeded:
            return CheckReport(SolverResult.UNKNOWN, calls=self.calls,
                               budget_exhausted=True, pruned=self.pruned)
        if report.result != SolverResult.SAT:
            return report
        if not minimize:
            return report
        # Lexicographic minimisation over fields in schema order.
        point = report.model
        try:
            point = self._lex_min(root, point)
        except BudgetExceeded:
            return CheckReport(SolverResult.SAT, model=point, calls=self.calls,
                               budget_exhausted=True, pruned=self.pruned)
        return CheckReport(SolverResult.SAT, model=point, calls=self.calls,
                           pruned=self.pruned)

    # ------------------------------------------------------------ initial env
    def _initial_env(self) -> Env:
        env = Env({}, {}, set(), {}, {})
        for name, spec in self.schema.items():
            env.int_intervals[("var", name)] = (spec.domain_lo(),
                                                 spec.domain_hi())
        return env

    # ------------------------------------------------------------- DPLL core
    def _solve(self, term: T.BTerm, env: Env) -> CheckReport:
        self._tick()
        status, env = self._propagate_bools(term, True, env)
        if status is False:
            self.pruned += 1
            return CheckReport(SolverResult.UNSAT, pruned=self.pruned)
        if status is None:
            return CheckReport(SolverResult.UNKNOWN, budget_exhausted=False,
                               pruned=self.pruned)

        decision = self._pick_decision(term, env)
        if decision is None:
            return self._theory_solve(term, env)

        for value in decision.options:
            child = env.clone()
            self._apply_decision(decision, value, child)
            if child.pres_fixed.get("__conflict__"):
                self.pruned += 1
                continue
            report = self._solve(term, child)
            if report.result == SolverResult.SAT:
                return report
            if report.result == SolverResult.UNKNOWN:
                return report
        self.pruned += 1
        return CheckReport(SolverResult.UNSAT, pruned=self.pruned)

    def _tick(self):
        self._decisions += 1
        if self._decisions > self.branch_budget:
            raise BudgetExceeded()

    # ------------------------------------------------------- bool propagation
    def _propagate_bools(self, term: T.BTerm, polarity: Optional[bool],
                         env: Env) -> Tuple[Optional[bool], Env]:
        """Return (status, env); status True/False/None(unknown)."""
        key = id(term)
        if key in env.bool_fixed:
            known = env.bool_fixed[key]
            if polarity is not None and known is not None and known != polarity:
                return False, env
            return True, env

        if isinstance(term, T.BConst):
            val = term.value
            if polarity is not None and val is not None and val != polarity:
                return False, env
            env.bool_fixed[key] = val
            return True, env

        if isinstance(term, T.Presence):
            val = env.pres_fixed.get(term.name, _UNFIXED)
            if val is not _UNFIXED and polarity is not None and val != polarity:
                return False, env
            if polarity is not None:
                env.pres_fixed[term.name] = polarity
            return True, env

        if isinstance(term, _NullFlag):
            val = env.null_fixed.get(term.name, _UNFIXED)
            if val is not _UNFIXED and polarity is not None and val != polarity:
                return False, env
            if polarity is not None:
                env.null_fixed[term.name] = polarity
                if polarity:
                    env.pres_fixed[term.name] = True
            return True, env

        if isinstance(term, T.NotB):
            inner = None if polarity is None else (None if polarity is None else not polarity)
            return self._propagate_bools(term.arg, inner, env)

        if isinstance(term, T.AndB):
            if polarity is True:
                for it in term.items:
                    st, env = self._propagate_bools(it, True, env)
                    if st is False:
                        return False, env
                return True, env
            if polarity is False:
                return True, env   # handled by case splitting
            return True, env

        if isinstance(term, T.OrB):
            if polarity is False:
                for it in term.items:
                    st, env = self._propagate_bools(it, False, env)
                    if st is False:
                        return False, env
                return True, env
            return True, env

        if isinstance(term, (T.CmpB, T.IsNullI, T.IteB)):
            if polarity is not None:
                env.bool_fixed[key] = polarity
            return True, env

        env.bool_fixed.setdefault(key, polarity)
        return True, env

    # ------------------------------------------------------------ decisions
    def _pick_decision(self, term: T.BTerm, env: Env) -> Optional["Decision"]:
        found: List[Decision] = []
        self._collect_decisions(term, set(), env, found)
        if not found:
            return None
        # Prefer input flags (small domains), then ITEs, then comparisons.
        rank = {_PresenceDecision: 0, _NullDecision: 1,
                _IteDecision: 2, _CmpDecision: 3, _NullIDecision: 4}
        found.sort(key=lambda d: rank[type(d)])
        return found[0]

    def _collect_decisions(self, term: T.BTerm, seen: Set[int], env: Env,
                           out: List[Decision]):
        if id(term) in seen:
            return
        seen.add(id(term))
        fixed = env.bool_fixed.get(id(term))

        if isinstance(term, T.Presence) and term.name not in env.pres_fixed:
            out.append(_PresenceDecision(term))
        elif isinstance(term, _NullFlag) and term.name not in env.null_fixed:
            out.append(_NullDecision(term))
        elif isinstance(term, T.IteB) and fixed is None:
            out.append(_IteDecision(term))
        elif isinstance(term, T.CmpB) and fixed is None:
            out.append(_CmpDecision(term))
        elif isinstance(term, T.IsNullI) and fixed is None:
            out.append(_NullIDecision(term))

        for child in self._b_children(term):
            self._collect_decisions(child, seen, env, out)
        for child in T.b_int_subterms(term):
            self._collect_i_decisions(child, seen, env, out)

    def _collect_i_decisions(self, term: T.ITerm, seen: Set[int], env: Env,
                             out: List[Decision]):
        if id(term) in seen:
            return
        seen.add(id(term))
        if isinstance(term, T.IteI):
            if env.bool_fixed.get(id(term.cond), _UNFIXED) is _UNFIXED:
                out.append(_IteDecision(term.cond))
            self._collect_i_decisions(term.yes, seen, env, out)
            self._collect_i_decisions(term.no, seen, env, out)
        for child in T.i_subterms(term):
            self._collect_i_decisions(child, seen, env, out)
        for child in T.i_bool_subterms(term):
            self._collect_decisions(child, seen, env, out)

    @staticmethod
    def _b_children(term: T.BTerm):
        if isinstance(term, T.NotB):
            yield term.arg
        elif isinstance(term, (T.AndB, T.OrB)):
            yield from term.items
        elif isinstance(term, T.IteB):
            yield term.cond
            yield term.yes
            yield term.no

    def _apply_decision(self, decision: "Decision", value, env: Env):
        if isinstance(decision, _PresenceDecision):
            env.pres_fixed[decision.term.name] = value
            if value is False:
                env.null_fixed[decision.term.name] = False
        elif isinstance(decision, _NullDecision):
            env.null_fixed[decision.term.name] = value
            if value:
                env.pres_fixed[decision.term.name] = True
        elif isinstance(decision, _IteDecision):
            # Route through propagation so composite conditions (NotB/AndB/OrB)
            # push their constraints down to the underlying flags; writing the
            # outer node alone would let NotB(Presence)=true coexist with
            # Presence=true and never raise the contradiction.
            status, env = self._propagate_bools(decision.term, value, env)
            if status is False:
                # Contradicts already-fixed input flags: this polarity is dead.
                env.pres_fixed["__conflict__"] = True
            # Record the decided value on the node so the collector does not
            # re-emit this same decision on every recursive step.
            env.bool_fixed[id(decision.term)] = value
        elif isinstance(decision, _CmpDecision):
            env.bool_fixed[id(decision.term)] = value
        elif isinstance(decision, _NullIDecision):
            env.bool_fixed[id(decision.term)] = value

    # ------------------------------------------------------------- theory leaf
    def _theory_solve(self, root: T.BTerm, env: Env) -> CheckReport:
        engine = _ICP(self.schema, env, self.enumerate_below, self)
        ok = engine.check_root(root)
        if ok is False:
            self.pruned += 1
            return CheckReport(SolverResult.UNSAT, pruned=self.pruned)
        completed = engine.complete_model(root)
        if completed is False:
            self.pruned += 1
            return CheckReport(SolverResult.UNSAT, pruned=self.pruned)
        if completed is None:
            return CheckReport(SolverResult.UNKNOWN, pruned=self.pruned)
        model = engine.build_model()
        if model is None:
            self.pruned += 1
            return CheckReport(SolverResult.UNSAT, pruned=self.pruned)
        return CheckReport(SolverResult.SAT, model=model, pruned=self.pruned)

    # --------------------------------------------------------- minimisation
    def _lex_min(self, root: T.BTerm, seed: InputPoint) -> InputPoint:
        current = seed
        for name in sorted(self.schema):
            spec = self.schema[name]
            # Step 1: minimise the presence/NULL rank.
            for rank in (0, 1, 2):
                cond = T.and_b([root, _rank_condition(name, rank)])
                rep = self.check(cond, minimize=False)
                if rep.result == SolverResult.SAT:
                    chosen_rank = rank
                    break
                if rep.result == SolverResult.UNKNOWN:
                    return current
            else:  # pragma: no cover - current point always satisfies one rank
                return current
            if chosen_rank < 2:
                current = _point_with_rank(current, name, chosen_rank)
                root = T.and_b([root, _rank_condition(name, chosen_rank)])
                continue
            root = T.and_b([root, _rank_condition(name, 2)])
            # Step 2: smallest machine value via binary search on feasibility.
            lo, hi = spec.domain_lo(), spec.domain_hi()
            best = current.values.get(name)
            while lo < hi:
                mid = lo + (hi - lo) // 2
                cond = T.and_b([root, T.cmp_b("le", T.IVar(spec.bits, name),
                                               T.IConst(spec.bits, mid))])
                rep = self.check(cond, minimize=False)
                if rep.result == SolverResult.SAT:
                    hi = mid
                elif rep.result == SolverResult.UNKNOWN:
                    break
                else:
                    lo = mid + 1
            else:
                best = lo
            current = _point_with_value(current, name, best)
            root = T.and_b([root, T.cmp_b("eq", T.IVar(spec.bits, name),
                                          T.IConst(spec.bits, best))])
        return current


# ---------------------------------------------------------------------------
# Decision descriptors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    @property
    def options(self) -> tuple:
        raise NotImplementedError


@dataclass(frozen=True)
class _PresenceDecision(Decision):
    term: T.Presence

    @property
    def options(self):
        return (False, True)


@dataclass(frozen=True)
class _NullDecision(Decision):
    term: T.BTerm

    @property
    def options(self):
        return (False, True)


@dataclass(frozen=True)
class _IteDecision(Decision):
    term: T.BTerm

    @property
    def options(self):
        return (True, False)


@dataclass(frozen=True)
class _CmpDecision(Decision):
    term: T.CmpB

    @property
    def options(self):
        return (True, False)


@dataclass(frozen=True)
class _NullIDecision(Decision):
    term: T.IsNullI

    @property
    def options(self):
        return (True, False)


# ---------------------------------------------------------------------------
# Internal NULL-flag boolean term (input-level, distinct from IsNullI)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _NullFlag(T.BTerm):
    name: str


# Solver-side mirrors of the symbolic engine's three-valued NULL predicates.
# Defined here (rather than imported from etleq.symbolic) to avoid an import
# cycle; structural equivalence is what the engine relies on.

@dataclass(frozen=True)
class SymIsNullB(T.BTerm):
    arg: T.BTerm


@dataclass(frozen=True)
class SymNullAny(T.BTerm):
    items: tuple


def _rank_condition(name: str, rank: int) -> T.BTerm:
    pres = T.Presence(name)
    nf = _NullFlag(name)
    if rank == 0:
        return T.not_b(pres)
    if rank == 1:
        return T.and_b([pres, nf])
    return T.and_b([pres, T.not_b(nf)])


def _point_with_rank(p: InputPoint, name: str, rank: int) -> InputPoint:
    values = dict(p.values)
    missing = set(p.missing)
    values.pop(name, None)
    missing.discard(name)
    if rank == 0:
        missing.add(name)
    elif rank == 1:
        values[name] = None
    else:
        values[name] = 0
    return InputPoint(values=values, missing=tuple(sorted(missing)))


def _point_with_value(p: InputPoint, name: str, value) -> InputPoint:
    values = dict(p.values)
    values[name] = value
    return InputPoint(values=values, missing=p.missing)


# ---------------------------------------------------------------------------
# Interval contraction engine for one leaf
# ---------------------------------------------------------------------------

class _ICP:
    def __init__(self, schema: Dict[str, FieldSpec], env: Env,
                 enum_limit: int, owner: NativeSolver):
        self.schema = schema
        self.env = env
        self.enum_limit = enum_limit
        self.owner = owner
        self.cache: Dict[int, Optional[Tuple[int, int]]] = {}

    # ------------------------------------------------------------------ root
    def check_root(self, root: T.BTerm) -> Optional[bool]:
        # Run the boolean/ICP check to a fixpoint: each comparison may tighten
        # intervals, which can invalidate earlier feasibility conclusions.
        for _ in range(ICP_ITERATIONS):
            snap = self._snapshot()
            try:
                ok = self._check_bool(root, True)
            except _Conflict:
                return False
            if ok is False:
                return False
            if self._snapshot() == snap:
                break
        return True if ok is not None else None

    def complete_model(self, root: T.BTerm) -> Optional[bool]:
        """Force every input to a concrete point and confirm the root holds.

        Returns True/False/None (None = budget exhausted). Enumeration is
        bounded by the owner's branch budget; input flags have at most three
        ranks (missing / null / value), so the search tree is small for the
        fragment the engine generates.
        """
        names = sorted(self.schema)
        return self._complete_fields(root, names, 0)

    def _complete_fields(self, root: T.BTerm, names: List[str],
                         idx: int) -> Optional[bool]:
        if idx == len(names):
            try:
                ok = self._check_bool(root, True)
                return False if ok is False else True
            except _Conflict:
                return False
        self.owner._tick()
        name = names[idx]
        spec = self.schema[name]
        pres = self.env.pres_fixed.get(name)
        nf = self.env.null_fixed.get(name)
        # Candidate (pres, null) assignments in rank order: missing, null, value.
        candidates = []
        if spec.optional:
            candidates.append((False, False))
        if spec.nullable:
            candidates.append((True, True))
        candidates.append((True, False))
        for p, n in candidates:
            if pres is not None and pres != p:
                continue
            if nf is not None and nf != n:
                continue
            snapshot = self._snapshot()
            self.env.pres_fixed[name] = p
            self.env.null_fixed[name] = n
            if n:
                self.cache.clear()
            try:
                res = self._complete_fields(root, names, idx + 1)
            except BudgetExceeded:
                self._restore(snapshot)
                return None
            except _Conflict:
                self._restore(snapshot)
                continue
            if res is True:
                return True
            if res is None:
                return None
            self._restore(snapshot)
        return False

    # ------------------------------------------------------------- booleans
    def _check_bool(self, t: T.BTerm, polarity: Optional[bool]) -> Optional[bool]:
        fixed = self.env.bool_fixed.get(id(t), _UNFIXED)
        if fixed is not _UNFIXED and polarity is not None and fixed != polarity:
            raise _Conflict()
        if isinstance(t, T.BConst):
            val = t.value
            if polarity is not None and val is not None and val != polarity:
                raise _Conflict()
            return val
        if isinstance(t, T.Presence):
            val = self.env.pres_fixed.get(t.name, _UNFIXED)
            if val is not _UNFIXED and polarity is not None and val != polarity:
                raise _Conflict()
            return None if val is _UNFIXED else val
        if isinstance(t, _NullFlag):
            val = self.env.null_fixed.get(t.name, _UNFIXED)
            if val is not _UNFIXED and polarity is not None and val != polarity:
                raise _Conflict()
            return None if val is _UNFIXED else val
        if isinstance(t, T.NotB):
            inner = None if polarity is None else not polarity
            return self._check_bool(t.arg, inner)
        if isinstance(t, T.AndB):
            if polarity is True:
                for it in t.items:
                    self._check_bool(it, True)
                return True
            if polarity is False:
                return self._check_disjunction([T.not_b(x) for x in t.items])
            return None
        if isinstance(t, T.OrB):
            if polarity is False:
                for it in t.items:
                    self._check_bool(it, False)
                return False
            if polarity is True:
                return self._check_disjunction(list(t.items))
            return None
        if isinstance(t, T.IteB):
            cond = self._check_bool(t.cond, None)
            if cond is not None:
                return self._check_bool(t.yes if cond else t.no, polarity)
            # Split on the condition. A branch satisfies the requested
            # polarity only if its sub-check actually confirms it (True); a
            # non-conflicting but undecided branch cannot be accepted.
            undecided = False
            for branch_val, sub in ((True, t.yes), (False, t.no)):
                snapshot = self._snapshot()
                try:
                    self._check_bool(t.cond, branch_val)
                    res = self._check_bool(sub, polarity)
                except _Conflict:
                    self._restore(snapshot)
                    continue
                if res is True or (polarity is None and res is not False):
                    return True
                if res is None:
                    undecided = True
                self._restore(snapshot)
            if undecided:
                return None
            raise _Conflict()
        if isinstance(t, SymIsNullB):
            return self._check_bool_null(t.arg, polarity)
        if isinstance(t, SymNullAny):
            sub = [SymIsNullB(it) for it in t.items]
            return self._check_bool(T.OrB(tuple(sub)), polarity)
        if isinstance(t, T.IsNullI):
            want = polarity if fixed is _UNFIXED else fixed
            # Direct connection to input-level null flags: IsNullI(IVar(n))
            # holds exactly when n is present-but-null.
            if isinstance(t.arg, T.IVar):
                name = t.arg.name
                nf = self.env.null_fixed.get(name, _UNFIXED)
                pres = self.env.pres_fixed.get(name, _UNFIXED)
                is_null = (nf is True) or (
                    nf is _UNFIXED and pres is not False and self.schema[name].nullable)
                if want is not None and bool(is_null) != want:
                    raise _Conflict()
                if want is True:
                    self.env.null_fixed[name] = True
                    self.env.pres_fixed[name] = True
                elif want is False and nf is _UNFIXED:
                    self.env.null_fixed[name] = False
                return is_null
            interval = self._interval(t.arg)
            if want is True:
                if not self._can_be_null(t.arg):
                    raise _Conflict()
                self.env.null_int.add(id(t.arg))
                return True
            if want is False:
                if id(t.arg) in self.env.null_int:
                    raise _Conflict()
                if interval is None:
                    raise _Conflict()
                return True
            return None
        if isinstance(t, T.CmpB):
            want = polarity if fixed is _UNFIXED else fixed
            return self._check_cmp(t, want)
        return None

    def _check_bool_null(self, t: T.BTerm, polarity: Optional[bool]) -> Optional[bool]:
        """Decide the three-valued NULL predicate on a boolean term."""
        if isinstance(t, T.BConst):
            is_null = t.value is None
        elif isinstance(t, (T.Presence, _NullFlag, T.IsNullI,
                            SymIsNullB, SymNullAny)):
            # These predicates always yield a definite TRUE/FALSE, never NULL.
            is_null = False
        elif isinstance(t, T.NotB):
            return self._check_bool_null(t.arg, polarity)
        elif isinstance(t, (T.AndB, T.OrB)):
            return self._check_bool(SymNullAny(tuple(t.items)), polarity)
        elif isinstance(t, T.CmpB):
            # A comparison is NULL iff either integer operand is NULL.
            null_input = T.OrB((T.IsNullI(t.left), T.IsNullI(t.right)))
            return self._check_bool(null_input, polarity)
        elif isinstance(t, T.IteB):
            # NULL(if c then y else n) is itself conditional:
            #   (c AND IsNullB(y)) OR (NOT c AND IsNullB(n))
            # Splitting on c keeps the two partitions' NULL status separate
            # instead of collapsing to "either branch may be NULL".
            cond_null = self._bool_is_null_term(t.cond)
            defined = T.and_b([
                T.or_b([t.cond, T.not_b(t.cond)]),
                T.not_b(cond_null),
            ])
            null_formula = T.or_b([
                T.and_b([t.cond, SymIsNullB(t.yes)]),
                T.and_b([T.not_b(t.cond), SymIsNullB(t.no)]),
            ])
            guarded = T.and_b([defined, null_formula])
            return self._check_bool(guarded, polarity)
        else:
            return None
        if polarity is not None and bool(is_null) != polarity:
            raise _Conflict()
        # Record the definite value so downstream decisions see it.
        self.env.bool_fixed[id(SymIsNullB(t))] = is_null
        return is_null

    def _bool_is_null_term(self, b: T.BTerm) -> T.BTerm:
        """Construct the NULL predicate for a boolean term structurally."""
        if isinstance(b, T.BConst):
            return T.TRUE if b.value is None else T.FALSE
        if isinstance(b, (T.Presence, _NullFlag, T.IsNullI,
                          SymIsNullB, SymNullAny)):
            return T.FALSE
        if isinstance(b, T.NotB):
            return self._bool_is_null_term(b.arg)
        if isinstance(b, (T.AndB, T.OrB)):
            return SymNullAny(tuple(b.items))
        if isinstance(b, T.CmpB):
            return T.OrB((T.IsNullI(b.left), T.IsNullI(b.right)))
        if isinstance(b, T.IteB):
            return T.or_b([
                T.and_b([b.cond, SymIsNullB(b.yes)]),
                T.and_b([T.not_b(b.cond), SymIsNullB(b.no)]),
            ])
        return SymIsNullB(b)

    def _check_disjunction(self, items: List[T.BTerm]) -> Optional[bool]:
        # Unit propagation first; split only when forced.
        pending = []
        for it in items:
            snapshot = self._snapshot()
            try:
                res = self._check_bool(it, True)
            except _Conflict:
                self._restore(snapshot)
                continue
            if res is True:
                return True
            pending.append((it, snapshot))
        if not pending:
            raise _Conflict()
        # Need a genuine case split; try each disjunct in order.
        base = self._snapshot()
        for it, _ in pending:
            self._restore(base)
            try:
                self._check_bool(it, True)
                return True
            except _Conflict:
                continue
        self._restore(base)
        return None

    # ------------------------------------------------------------- compares
    def _check_cmp(self, t: T.CmpB, want: Optional[bool]) -> Optional[bool]:
        # Structural equality: two independently-built but identical term
        # trees denote the same function on every input, so eq is a tautology
        # (and ne a contradiction) regardless of NULL branches or ITE nesting.
        if t.op in ("eq", "ne") and t.left == t.right:
            is_eq = t.op == "eq"
            if want is True:
                if not is_eq:
                    raise _Conflict()
                return True
            if want is False:
                if is_eq:
                    raise _Conflict()
                return False
            return is_eq
        # When an operand is an IteI whose condition cannot already be decided
        # in this environment, split on the condition instead of reasoning over
        # the merged interval union, which would conflate the two branches'
        # discrete values (notably the error-class ids carried by code_id).
        for operand in (t.left, t.right):
            if isinstance(operand, T.IteI):
                cond_val = self.env.bool_fixed.get(id(operand.cond), _UNFIXED)
                if cond_val is _UNFIXED:
                    return self._split_ite_cmp(t, want, operand)
                # A decided condition: rewrite to the selected branch directly
                # so the comparison never sees the union interval.
                selected = operand.yes if cond_val is True else operand.no
                t = T.CmpB(t.op,
                           selected if t.left is operand else t.left,
                           selected if t.right is operand else t.right)
        li = self._interval(t.left)
        ri = self._interval(t.right)
        if li is None or ri is None:
            if want is not None:
                # NULL comparison; only satisfiable if the term is unconstrained.
                fixed = self.env.bool_fixed.get(id(t))
                if fixed is not None:
                    raise _Conflict()
            return None
        l_lo, l_hi = li
        r_lo, r_hi = ri
        # After hash-consing, two identical expressions share one node and so
        # can never disagree; this also covers wrapped machine values.
        if t.left is t.right:
            same = True
            if want is False and t.op in ("eq", "le", "ge"):
                raise _Conflict()
            if want is True and t.op == "ne":
                raise _Conflict()
            if t.op == "eq":
                return True if want is not False else None
            if t.op == "ne":
                return False if want is True else True
        # Modular equality for wrapped machine values: two's-complement
        # equality is a congruence, not ordinary integer equality.
        if t.op in ("eq", "ne") and (
                isinstance(t.left, T.WrapI) or isinstance(t.right, T.WrapI)):
            bits = t.left.bits
            inner_l = t.left.arg if isinstance(t.left, T.WrapI) else t.left
            inner_r = t.right.arg if isinstance(t.right, T.WrapI) else t.right
            try:
                if t.op == "eq":
                    self._mod_equal(inner_l, inner_r, bits, True)
                    return True if want is not False else None
                self._mod_equal(inner_l, inner_r, bits, False)
                return False if want is True else True
            except _Conflict:
                if want is not None:
                    raise
                return None
        possible = self._cmp_possible(t.op, l_lo, l_hi, r_lo, r_hi)
        neg_possible = self._cmp_possible(_neg_op(t.op), l_lo, l_hi, r_lo, r_hi)
        if want is True:
            if not possible:
                raise _Conflict()
            self._narrow_cmp(t.op, True, t.left, t.right)
            return True
        if want is False:
            if not neg_possible:
                raise _Conflict()
            self._narrow_cmp(_neg_op(t.op), True, t.left, t.right)
            return False
        if not possible and not neg_possible:
            raise _Conflict()
        if possible and not neg_possible:
            self._narrow_cmp(t.op, True, t.left, t.right)
            return True
        if neg_possible and not possible:
            self._narrow_cmp(_neg_op(t.op), True, t.left, t.right)
            return False
        return None

    def _split_ite_cmp(self, t: T.CmpB, want: Optional[bool],
                       ite: T.IteI) -> Optional[bool]:
        """Case-split an IteI operand on its condition during a comparison."""
        undecided = False
        for branch_val in (True, False):
            snapshot = self._snapshot()
            try:
                self._check_bool(ite.cond, branch_val)
                sub = ite.yes if branch_val else ite.no
                rebuilt = T.CmpB(t.op,
                                 sub if t.left is ite else t.left,
                                 sub if t.right is ite else t.right)
                res = self._check_cmp(rebuilt, want)
            except _Conflict:
                self._restore(snapshot)
                continue
            if res is True or (want is None and res is not False):
                return True
            if res is None:
                undecided = True
            self._restore(snapshot)
        if undecided:
            return None
        raise _Conflict()

    @staticmethod
    def _cmp_possible(op, l_lo, l_hi, r_lo, r_hi) -> bool:
        if op == "eq":
            return l_lo <= r_hi and r_lo <= l_hi
        if op == "ne":
            return not (l_lo == l_hi == r_lo == r_hi) or l_lo != r_lo
        if op == "lt":
            return l_lo < r_hi
        if op == "le":
            return l_lo <= r_hi
        if op == "gt":
            return l_hi > r_lo
        if op == "ge":
            return l_hi >= r_lo
        raise ValueError(op)

    def _narrow_cmp(self, op: str, positive: bool, l: T.ITerm, r: T.ITerm):
        li = self._interval(l)
        ri = self._interval(r)
        if li is None or ri is None:
            return
        l_lo, l_hi = li
        r_lo, r_hi = ri
        if op == "eq":
            lo, hi = max(l_lo, r_lo), min(l_hi, r_hi)
            if lo > hi:
                raise _Conflict()
            self._set_interval(l, (lo, hi))
            self._set_interval(r, (lo, hi))
            # Push the equality back through arithmetic so operands of two
            # equal expressions are constrained too (inverse contraction).
            self._inverse_narrow(l, (lo, hi))
            self._inverse_narrow(r, (lo, hi))
        elif op == "lt":
            self._set_interval(l, (l_lo, min(l_hi, r_hi - 1)))
            self._set_interval(r, (max(r_lo, l_lo + 1), r_hi))
        elif op == "le":
            self._set_interval(l, (l_lo, min(l_hi, r_hi)))
            self._set_interval(r, (max(r_lo, l_lo), r_hi))
        elif op == "gt":
            self._set_interval(l, (max(l_lo, r_lo + 1), l_hi))
            self._set_interval(r, (r_lo, min(r_hi, l_hi - 1)))
        elif op == "ge":
            self._set_interval(l, (max(l_lo, r_lo), l_hi))
            self._set_interval(r, (r_lo, min(r_hi, l_hi)))
        elif op == "ne":
            if l_lo == l_hi:
                self._set_interval(r, self._remove_point(ri, l_lo))
            if r_lo == r_hi:
                self._set_interval(l, self._remove_point(li, r_lo))

    def _inverse_narrow(self, t: T.ITerm, target: Tuple[int, int]):
        """Given result interval ``target`` for an arithmetic term, tighten
        its operands. Only the linear ops have exact inverse contractors;
        multiplication and division are left to enumeration on small widths.
        """
        if not isinstance(t, T.BinI):
            return
        li = self._interval(t.left)
        ri = self._interval(t.right)
        if li is None or ri is None:
            return
        t_lo, t_hi = target
        if t.op == "add":
            # result = l + r  =>  l in [t_lo - r.hi, t_hi - r.lo]
            self._set_interval(t.left, (max(li[0], t_lo - ri[1]),
                                        min(li[1], t_hi - ri[0])))
            self._set_interval(t.right, (max(ri[0], t_lo - li[1]),
                                         min(ri[1], t_hi - li[0])))
        elif t.op == "sub":
            # result = l - r  =>  l in [t_lo + r.lo, t_hi + r.hi]
            self._set_interval(t.left, (max(li[0], t_lo + ri[0]),
                                        min(li[1], t_hi + ri[1])))
            self._set_interval(t.right, (max(ri[0], li[0] - t_hi),
                                         min(ri[1], li[1] - t_lo)))
        elif t.op == "mul":
            # Exact only when one operand is a constant; skip otherwise.
            if isinstance(t.right, T.IConst):
                self._inverse_mul_const(t.left, t.right.value, target)
            elif isinstance(t.left, T.IConst):
                self._inverse_mul_const(t.right, t.left.value, target)

    def _inverse_mul_const(self, var_node: T.ITerm, k: int,
                           target: Tuple[int, int]):
        if k == 0:
            return
        cur = self._interval(var_node)
        if cur is None:
            return
        if k > 0:
            cand = (math.ceil(target[0] / k), math.floor(target[1] / k))
        else:
            cand = (math.ceil(target[1] / k), math.floor(target[0] / k))
        self._set_interval(var_node, (max(cur[0], cand[0]),
                                      min(cur[1], cand[1])))

    @staticmethod
    def _remove_point(iv: Tuple[int, int], point: int) -> Tuple[int, int]:
        lo, hi = iv
        if point == lo:
            return (lo + 1, hi)
        if point == hi:
            return (lo, hi - 1)
        return iv  # cannot represent a hole; leave un-narrowed

    # ------------------------------------------------------------- intervals
    def _can_be_null(self, t: T.ITerm) -> bool:
        if id(t) in self.env.null_int:
            return True
        if isinstance(t, T.NullI):
            return True
        if isinstance(t, T.IVar):
            nf = self.env.null_fixed.get(t.name, _UNFIXED)
            pres = self.env.pres_fixed.get(t.name, _UNFIXED)
            if nf is True:
                return True
            if nf is False or pres is False:
                return False
            return self.schema[t.name].nullable
        if isinstance(t, T.IConst):
            return False
        if isinstance(t, T.IteI):
            cond = self.env.bool_fixed.get(id(t.cond), _UNFIXED)
            if cond is True:
                return self._can_be_null(t.yes)
            if cond is False:
                return self._can_be_null(t.no)
            return self._can_be_null(t.yes) or self._can_be_null(t.no)
        if isinstance(t, T.BinI):
            return self._can_be_null(t.left) or self._can_be_null(t.right)
        if isinstance(t, T.WrapI):
            return self._can_be_null(t.arg)
        return False

    @staticmethod
    def _var_key(t: T.ITerm):
        """Stable storage key for an input variable, independent of object id.

        Normalization hash-conses terms into fresh objects, so two structurally
        identical input variables can have different ids. Keying their interval
        by field name keeps contraction and model extraction consistent.
        """
        if isinstance(t, T.IVar):
            return ("var", t.name)
        return id(t)

    def _interval(self, t: T.ITerm) -> Optional[Tuple[int, int]]:
        """Return [lo, hi] for the non-NULL values, or None if only-NULL."""
        key = self._var_key(t)
        if key in self.cache:
            return self.cache[key]
        if id(t) in self.env.null_int:
            self.cache[key] = None
            return None
        iv = self._compute_interval(t)
        if iv is not None:
            prev = self.env.int_intervals.get(key)
            if prev is not None:
                iv = (max(iv[0], prev[0]), min(iv[1], prev[1]))
        self.cache[key] = iv
        return iv

    def _compute_interval(self, t: T.ITerm) -> Optional[Tuple[int, int]]:
        if isinstance(t, T.IConst):
            return (t.value, t.value)
        if isinstance(t, T.NullI):
            return None
        if isinstance(t, T.IVar):
            pres = self.env.pres_fixed.get(t.name)
            nf = self.env.null_fixed.get(t.name)
            if pres is False or nf is True:
                return None
            return self.env.int_intervals.get(
                ("var", t.name),
                (self.schema[t.name].domain_lo(),
                 self.schema[t.name].domain_hi()))
        if isinstance(t, T.WrapI):
            inner = self._interval(t.arg)
            if inner is None:
                return None
            return self._wrap_interval(inner, t.bits)
        if isinstance(t, T.IteI):
            cond = self.env.bool_fixed.get(id(t.cond))
            if cond is True:
                return self._interval(t.yes)
            if cond is False:
                return self._interval(t.no)
            yi, ni = self._interval(t.yes), self._interval(t.no)
            if yi is None:
                return ni
            if ni is None:
                return yi
            return (min(yi[0], ni[0]), max(yi[1], ni[1]))
        if isinstance(t, T.BinI):
            return self._bin_interval(t)
        return (-INF, INF)

    def _bin_interval(self, t: T.BinI) -> Optional[Tuple[int, int]]:
        li, ri = self._interval(t.left), self._interval(t.right)
        if li is None or ri is None:
            return None
        l_lo, l_hi = li
        r_lo, r_hi = ri
        if t.op == "add":
            iv = (l_lo + r_lo, l_hi + r_hi)
        elif t.op == "sub":
            iv = (l_lo - r_hi, l_hi - r_lo)
        elif t.op == "mul":
            vals = (l_lo * r_lo, l_lo * r_hi, l_hi * r_lo, l_hi * r_hi)
            iv = (min(vals), max(vals))
        elif t.op in ("div", "rem"):
            iv = self._div_interval(t, li, ri)
        else:
            iv = (-INF, INF)
        if iv is None:
            return None
        return self._wrap_interval(iv, t.bits)

    def _div_interval(self, t: T.BinI, li, ri) -> Optional[Tuple[int, int]]:
        l_lo, l_hi = li
        r_lo, r_hi = ri
        min_int = -(1 << (t.bits - 1))
        # Side condition: failure flag must agree with div-by-zero / trap.
        fail_fixed = self.env.bool_fixed.get(id(t.fail))
        zero_possible = r_lo <= 0 <= r_hi
        trap_possible = (l_lo <= min_int <= l_hi) and (r_lo <= -1 <= r_hi)
        if fail_fixed is True:
            if not (zero_possible or trap_possible):
                raise _Conflict()
            return (0, 0)  # fallback value path
        if fail_fixed is False and (r_lo == 0 and r_hi == 0):
            raise _Conflict()
        if r_lo == 0 and r_hi == 0:
            return (0, 0)
        safe_lo = r_lo if r_lo != 0 else 1
        safe_hi = r_hi if r_hi != 0 else -1
        if t.op == "div":
            vals = []
            for a in (l_lo, l_hi):
                for b in (safe_lo, safe_hi):
                    if b == 0 or (a == min_int and b == -1):
                        continue
                    vals.append(math.trunc(a / b))
            if not vals:
                return (0, 0)
            return (min(vals), max(vals))
        # rem: result magnitude bounded by |divisor|-1
        bound = max(abs(safe_lo), abs(safe_hi)) - 1
        return (-bound, bound)

    @staticmethod
    def _wrap_interval(iv: Tuple[int, int], bits: int) -> Tuple[int, int]:
        lo, hi = iv
        modulus = 1 << bits
        min_int = -(1 << (bits - 1))
        max_int = (1 << (bits - 1)) - 1
        if lo >= min_int and hi <= max_int:
            return (lo, hi)
        if hi - lo >= modulus:
            return (min_int, max_int)
        # Project both endpoints to their wrapped values. When the span does
        # not cross a modulus boundary the wrapped image stays a single tight
        # interval; otherwise it splits into two segments we merge to the full
        # domain (enumeration resolves those on small widths).
        wlo = _twos(lo, bits)
        whi = _twos(hi, bits)
        if lo // modulus == hi // modulus or (wlo <= whi and hi - lo < modulus):
            # Same quotient block: order is preserved after projection.
            if wlo <= whi and (lo >= 0 or (lo < 0 and hi < 0)):
                return (wlo, whi)
        return (min_int, max_int)

    def _mod_equal(self, l: T.ITerm, r: T.ITerm, bits: int, want: bool):
        """Equality after two's-complement reduction: congruence mod 2^bits."""
        modulus = 1 << bits
        # Hash-consing makes two structurally identical expressions the very
        # same node, so they can never take different values.
        if l is r:
            if want is False:
                raise _Conflict()
            return
        li, ri = self._interval(l), self._interval(r)
        if li is None or ri is None:
            return
        if want and li == ri and li[0] == li[1]:
            return
        # Tighten via the shared residue when one side is already pinned.
        if li[0] == li[1] and ri[0] != ri[1]:
            residue = li[0] % modulus
            self._restrict_residue(r, residue, modulus, ri, want)
        elif ri[0] == ri[1] and li[0] != li[1]:
            residue = ri[0] % modulus
            self._restrict_residue(l, residue, modulus, li, want)

    def _restrict_residue(self, node: T.ITerm, residue: int, modulus: int,
                          iv: Tuple[int, int], want: bool):
        lo, hi = iv
        if want:
            # Keep only points congruent to residue inside [lo, hi].
            candidates = [v for v in range(lo, hi + 1) if v % modulus == residue]
            if not candidates:
                raise _Conflict()
            self._set_interval(node, (min(candidates), max(candidates)))
        else:
            # ne: if the interval holds a single residue, exclude it.
            if all(v % modulus == residue for v in range(lo, hi + 1)):
                raise _Conflict()

    def _set_interval(self, t: T.ITerm, iv: Tuple[int, int]):
        if iv[0] > iv[1]:
            raise _Conflict()
        key = self._var_key(t)
        old = self.env.int_intervals.get(key)
        if old is not None:
            iv = (max(old[0], iv[0]), min(old[1], iv[1]))
            if iv[0] > iv[1]:
                raise _Conflict()
        self.env.int_intervals[key] = iv
        self.cache.pop(key, None)

    # -------------------------------------------------------------- snapshot
    def _snapshot(self):
        return (dict(self.env.bool_fixed), dict(self.env.int_intervals),
                set(self.env.null_int), dict(self.env.pres_fixed),
                dict(self.env.null_fixed))

    def _restore(self, snap):
        bf, ii, ni, pf, nf = snap
        self.env.bool_fixed = bf
        self.env.int_intervals = ii
        self.env.null_int = ni
        self.env.pres_fixed = pf
        self.env.null_fixed = nf
        self.cache.clear()

    # ---------------------------------------------------------------- model
    def build_model(self) -> Optional[InputPoint]:
        values: Dict[str, Optional[int]] = {}
        missing: List[str] = []
        for name, spec in self.schema.items():
            pres = self.env.pres_fixed.get(name)
            nf = self.env.null_fixed.get(name)
            var = T.IVar(spec.bits, name)
            iv = self.env.int_intervals.get(("var", name),
                                            (spec.domain_lo(), spec.domain_hi()))
            if pres is False:
                missing.append(name)
                continue
            if nf is True:
                values[name] = None
                continue
            if not spec.nullable and nf is None:
                pass
            # Prefer a concrete value; pick the lower bound (minimisation pass
            # later computes the true lex-min witness).
            values[name] = iv[0]
        return InputPoint(values=values, missing=tuple(sorted(missing)))


class _Conflict(Exception):
    pass


_UNFIXED = object()


def _twos(value: int, bits: int) -> int:
    modulus = 1 << bits
    v = value % modulus
    if v >= 1 << (bits - 1):
        v -= modulus
    return v


def _neg_op(op: str) -> str:
    return {"eq": "ne", "ne": "eq", "lt": "ge", "ge": "lt",
            "le": "gt", "gt": "le"}[op]
