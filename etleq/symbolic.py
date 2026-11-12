"""Path-based symbolic execution engine.

The engine executes the statement IR and keeps a *set of states*.  Every
state is one feasible (path constraint, store, output, error) tuple; branch
joins merge states back together so multi-level branch convergence does not
blow up exponentially.

Key design points
-----------------
* Feasibility pruning happens at every fork via the solver: a branch whose
  path constraint is unsat is dropped immediately and counted.
* Merging at joins uses if-then-else terms on both the integer and boolean
  sorts, keeping path constraints, output fields and the error classification
  aligned inside a single merged state.
* Loops are statically unrolled up to the rule's bound; exceeding it produces
  ``E_LOOP_BOUND`` and the driver can still reason about everything below the
  bound.
* The final summary is a set of states covering the complete finite input
  domain (every input satisfies exactly one state's path constraint).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import errors as E
from . import terms as T
from .ast import (
    EAnd, EArith, ECmp, EError, EField, EIf, EInt, EMissing, ENot, ENullTest,
    EOr, EVar, EWrap, Expr, OutputSpec, Rule, SIf, SLet, SLoop, SSet, SEmit,
    SFail, Stmt,
)
from .solver.interface import (
    ErrorOutcome, O_MISSING, O_NULL, O_VALUE, Outcome, Solver, SolverResult,
)
from .solver.native import _NullFlag, SymIsNullB as _IsNullB, SymNullAny as _NullAny


@dataclass
class SymState:
    path: T.BTerm                       # conjunction of decisions so far
    store: Dict[str, T.ITerm]           # locals -> machine int terms (may be NullI)
    outputs: Dict[str, Outcome]
    raised: T.BTerm                     # has this state failed?
    code_id: T.ITerm                    # error class id when raised
    active: bool = True                 # False once the state has terminated
    depth: int = 0
    # Failures discovered while evaluating an expression, drained at the next
    # statement boundary. Each entry is (guard, code). Order matters: the
    # concrete interpreter short-circuits on the first failure.
    pending: List[Tuple[T.BTerm, str]] = field(default_factory=list)


@dataclass
class EngineStats:
    states_peak: int = 0
    forks: int = 0
    merges: int = 0
    pruned_branches: int = 0
    loop_unrolls: int = 0
    solver_calls: int = 0
    unknown_branches: int = 0


@dataclass
class SymSummary:
    states: Tuple[SymState, ...]
    output_order: Tuple[str, ...]
    stats: EngineStats
    complete: bool                      # False if a branch returned UNKNOWN


class SymbolicEngine:
    def __init__(self, rule: Rule, solver: Solver, codes: Tuple[str, ...]):
        self.rule = rule
        self.solver = solver
        self.codes = codes
        self.stats = EngineStats()

    # ------------------------------------------------------------------ run
    def execute(self) -> SymSummary:
        initial = SymState(
            path=T.TRUE,
            store={},
            outputs={
                o.name: Outcome(o.bits, T.FALSE, T.FALSE, T.NullI(o.bits))
                for o in self.rule.outputs
            },
            raised=T.FALSE,
            code_id=T.IConst(32, 0),
        )
        states = [initial]
        states = self._exec_block(self.rule.block, states)
        complete = self.stats.unknown_branches == 0
        return SymSummary(tuple(states), tuple(o.name for o in self.rule.outputs),
                          self.stats, complete)

    # ------------------------------------------------------------ statement
    def _drain_pending(self, st: SymState) -> List[SymState]:
        """Materialise failures discovered during expression evaluation.

        Pending guards are applied in order against the surviving partition,
        which reproduces the concrete interpreter's first-failure ordering.
        """
        if not st.pending:
            return [st]
        pending = list(st.pending)
        st.pending = []
        current = [st]
        for guard, code in pending:
            nxt: List[SymState] = []
            for part in current:
                if not part.active:
                    nxt.append(part)
                    continue
                nxt.extend(self._fail_partition(part, guard, code, "expr"))
            current = nxt
        return current

    def _exec_block(self, stmts: Tuple[Stmt, ...],
                    states: List[SymState]) -> List[SymState]:
        for s in stmts:
            drained: List[SymState] = []
            for st in states:
                drained.extend(self._drain_pending(st))
            states = self._exec(s, drained)
            self.stats.states_peak = max(self.stats.states_peak, len(states))
        return states

    def _exec(self, s: Stmt, states: List[SymState]) -> List[SymState]:
        result: List[SymState] = []
        for st in states:
            if not st.active:
                result.append(st)
                continue
            if isinstance(s, (SLet, SSet)):
                st.store[s.name] = self._eval_int(s.value, st)
                result.append(st)
            elif isinstance(s, SFail):
                result.extend(self._fail_partition(
                    st, T.TRUE, E.explicit_error(s.code), s.loc))
            elif isinstance(s, SEmit):
                result.extend(self._emit(st, s))
            elif isinstance(s, SIf):
                result.extend(self._fork_if(s, st))
            elif isinstance(s, SLoop):
                result.extend(self._exec_loop(s, st))
            else:
                result.append(st)
        # Normalise: every element must be a SymState, never a nested list.
        flat: List[SymState] = []
        for item in result:
            if isinstance(item, SymState):
                flat.append(item)
            else:
                flat.extend(item)
        return flat

    def _emit(self, st: SymState, s: SEmit) -> List[SymState]:
        spec = next(o for o in self.rule.outputs if o.name == s.output)
        value = self._eval_int(s.value, st)
        isnull = T.IsNullI(value)
        if not spec.nullable:
            # Non-nullable output: a NULL value is a classified failure. Split
            # the state into a failing partition and a surviving one.
            parts = self._fail_partition(st, isnull, E.E_NULL_OUTPUT, s.loc)
            for p in parts:
                if p.active:
                    p.outputs[s.output] = Outcome(
                        spec.bits, T.TRUE, T.FALSE,
                        value if not isinstance(value, T.NullI)
                        else T.NullI(spec.bits))
                else:
                    p.outputs[s.output] = Outcome(
                        spec.bits, T.TRUE, T.TRUE, T.NullI(spec.bits))
            return parts
        st.outputs[s.output] = Outcome(
            spec.bits, T.TRUE, isnull,
            value if not isinstance(value, T.NullI) else T.NullI(spec.bits))
        return [st]

    def _fail_partition(self, st: SymState, guard: T.BTerm,
                        code: str, loc: str) -> List[SymState]:
        """Split a state on a failure guard.

        Returns one or two states:

        * the *failing* partition (path & guard), marked terminated, carrying
          this classification unless it had already failed earlier;
        * the *surviving* partition (path & not guard), left active so the
          remaining statements execute exactly as the concrete short-circuit
          semantics require.

        Infeasible partitions are pruned via the solver and counted.
        """
        cid = self.codes.index(code)
        # An unconditional failure (constant-true guard) terminates the state
        # in place. Its path already encodes every branch decision that led
        # here, so it must not be narrowed or rebuilt -- doing so would make a
        # failing then-branch overlap the else-branch at the join.
        if isinstance(guard, T.BConst) and guard.value is True:
            st.code_id = T.ite_i(st.raised, st.code_id, T.IConst(32, cid))
            st.raised = T.TRUE
            st.active = False
            return [st]
        fail_pc = T.and_b([st.path, guard])
        survive_pc = T.and_b([st.path, T.not_b(guard)])
        fail_verdict = self._feasible(fail_pc)
        survive_verdict = self._feasible(survive_pc)
        out: List[SymState] = []

        if fail_verdict != SolverResult.UNSAT:
            dead = self._clone(st, fail_pc)
            dead.raised = T.TRUE
            dead.code_id = T.ite_i(st.raised, st.code_id, T.IConst(32, cid))
            dead.active = False
            if fail_verdict == SolverResult.UNKNOWN:
                self.stats.unknown_branches += 1
            out.append(dead)
        else:
            self.stats.pruned_branches += 1

        if survive_verdict != SolverResult.UNSAT:
            live = self._clone(st, survive_pc)
            live.active = True
            if survive_verdict == SolverResult.UNKNOWN:
                self.stats.unknown_branches += 1
            out.append(live)
        else:
            self.stats.pruned_branches += 1
        return out

    def _raise(self, st: SymState, code: str, loc: str,
               guard: Optional[T.BTerm] = None):
        """Unconditional failure of an entire state (e.g. a branch join)."""
        cid = self.codes.index(code)
        st.raised = T.TRUE
        st.code_id = T.ite_i(st.raised, st.code_id, T.IConst(32, cid))
        st.active = False

    # ------------------------------------------------------------- branching
    def _fork_if(self, s: SIf, st: SymState) -> List[SymState]:
        # Failures discovered while evaluating the condition are drained
        # first. The common case is a single surviving state; a pre-branch
        # failure only happens when condition evaluation itself faulted.
        survivors = self._drain_pending(st)
        if len(survivors) == 1 and survivors[0].active:
            # Fast path: no pre-branch failure, the live fork's own join is the
            # only merge needed. Re-wrapping it would replace its disjoint
            # sibling selectors with an OR-polluted path.
            return self._fork_live_if(s, survivors[0])
        results: List[SymState] = []
        for alive in survivors:
            if not alive.active:
                results.append(alive)
                continue
            results.extend(self._fork_live_if(s, alive))
        if len(results) == 1:
            return results
        # A genuine pre-branch failure partition plus the branch join: their
        # paths are already disjoint (the failure guard was conjoined at drain
        # time), so a single top-level merge restoring the parent path is sound.
        return [self._merge_join(results, parent_path=st.path)]

    def _fork_live_if(self, s: SIf, st: SymState) -> List[SymState]:
        cond = self._eval_bool(s.cond, st)
        null_case = self._bool_is_null(cond)
        # Plain Python booleans label the two definite branches; comparing a
        # BConst singleton with ``is True`` would always be false and silently
        # swap then/else.
        branches = [(True, s.then), (False, s.els)]
        children: List[SymState] = []
        for cond_val, block in branches:
            guard = T.and_b([T.not_b(null_case),
                             cond if cond_val else T.not_b(cond)])
            pc = T.and_b([st.path, guard])
            verdict = self._feasible(pc)
            if verdict == SolverResult.UNSAT:
                self.stats.pruned_branches += 1
                continue
            child = self._clone(st, pc)
            if verdict == SolverResult.UNKNOWN:
                self.stats.unknown_branches += 1
            child.depth = st.depth + 1
            block_states = self._exec_block(block, [child])
            # Every path inside the block can be pruned as infeasible; skip
            # this branch rather than indexing into an empty result.
            if not block_states:
                self.stats.pruned_branches += 1
                continue
            child = block_states[0]
            children.append(child)
        null_guard = null_case
        null_pc = T.and_b([st.path, null_guard])
        null_verdict = self._feasible(null_pc)
        if null_verdict != SolverResult.UNSAT:
            null_state = self._clone(st, null_pc)
            null_state.depth = st.depth + 1
            self._raise(null_state, E.E_NULL_BRANCH, s.loc)
            children.append(null_state)
            if null_verdict == SolverResult.UNKNOWN:
                self.stats.unknown_branches += 1
        else:
            self.stats.pruned_branches += 1
        self.stats.forks += 1
        # Re-converge: siblings are mutually exclusive and exhaust the parent's
        # input space. The merge restores the parent's path while the sibling
        # paths remain the ITE selectors inside the merged terms.
        return [self._merge_join(children, parent_path=st.path)]

    def _merge_join(self, states: List[SymState],
                    parent_path: Optional[T.BTerm] = None) -> SymState:
        if not states:
            # All partitions were pruned; return a permanently-infeasible state
            # that the caller can detect via its contradictory FALSE path.
            return SymState(path=T.FALSE, store={},
                            outputs={}, raised=T.FALSE,
                            code_id=T.IConst(32, 0), active=False)
        if len(states) == 1:
            return states[0]
        live = [s for s in states if s.active]
        base = states[0]
        # The fork's siblings are mutually exclusive and jointly exhaustive over
        # the parent's input space. The merged state keeps the PARENT's path:
        # sibling distinctions are encoded in the path-indexed ITE terms, while
        # any outer constraint (e.g. a loop's bound==n guard) must survive the
        # join. At the rule top level the parent path is simply TRUE.
        merged_path = parent_path if parent_path is not None else states[0].path
        merged_store: Dict[str, T.ITerm] = {}
        names: set = set()
        for s in states:
            names.update(s.store)
        for name in names:
            width = next(s.store[name].bits for s in states
                         if name in s.store and not isinstance(s.store[name], T.NullI)) \
                if any(name in s.store for s in states) else 32
            merged_store[name] = self._merge_int(states, name, width)
        merged_outputs: Dict[str, Outcome] = {}
        for oname in base.outputs:
            merged_outputs[oname] = self._merge_outcome(states, oname)
        # raised is path-indexed (partitions are mutually exclusive), never a
        # plain OR whose constant folding could collapse three-valued guards.
        merged_raised = self._merge_bool(states, lambda s: s.raised)
        merged_code = self._merge_code(states)
        merged = SymState(merged_path, merged_store, merged_outputs,
                          merged_raised, merged_code,
                          active=bool(live),
                          depth=max(s.depth for s in states))
        self.stats.merges += 1
        return merged

    def _merge_int(self, states: List[SymState], name: str,
                   width: int) -> T.ITerm:
        def term_of(s: SymState) -> T.ITerm:
            t = s.store.get(name)
            return t if t is not None else T.NullI(width)
        out = term_of(states[-1])
        for idx in reversed(range(len(states) - 1)):
            out = T.ite_i(self._selector(states, idx), term_of(states[idx]), out)
        return out

    def _merge_outcome(self, states: List[SymState], name: str) -> Outcome:
        outs = [s.outputs[name] for s in states]
        bits = outs[0].bits
        present = outs[-1].present
        isnull = outs[-1].isnull
        value = outs[-1].value
        for idx in reversed(range(len(states) - 1)):
            sel = self._selector(states, idx)
            o = outs[idx]
            present = T.ite_b(sel, o.present, present)
            isnull = T.ite_b(sel, o.isnull, isnull)
            value = T.ite_i(sel, o.value, value)
        return Outcome(bits, present, isnull, value)

    def _merge_code(self, states: List[SymState]) -> T.ITerm:
        out = states[-1].code_id
        for idx in reversed(range(len(states) - 1)):
            out = T.ite_i(self._selector(states, idx),
                          states[idx].code_id, out)
        return out

    def _merge_bool(self, states: List[SymState], pick) -> T.BTerm:
        out = pick(states[-1])
        for idx in reversed(range(len(states) - 1)):
            out = T.ite_b(self._selector(states, idx),
                          pick(states[idx]), out)
        return out

    @staticmethod
    def _selector(states: List[SymState], idx: int) -> T.BTerm:
        """Condition selecting sibling ``idx`` inside one fork's merge.

        All siblings in a merge originate from one fork, so their paths are
        mutually exclusive and jointly exhaustive over the parent's inputs.
        The accumulated absolute path is therefore the correct, disjoint ITE
        selector.
        """
        return states[idx].path

    # ---------------------------------------------------------------- loops
    def _exec_loop(self, s: SLoop, st: SymState) -> List[SymState]:
        bound = self._eval_int(s.bound, st)
        # Bound must be a concrete non-negative constant for static unrolling.
        if not isinstance(bound, T.IConst):
            # Symbolic bound: keep one mutually-exclusive state per feasible
            # bound value rather than nesting them into a deep IteI chain.
            # Each state's path pins the bound, so later queries stay shallow
            # and the solver resolves them by finite (bounded) enumeration.
            return self._symbolic_bound_loop(s, st, bound)
        n = bound.value
        if n < 0 or n > s.max_bound:
            self._raise(st, E.E_LOOP_BOUND, s.loc)
            return [st]
        current = st
        for name, init in s.inits:
            current.store[name] = self._eval_int(init, current)
        states = [current]
        for i in range(n):
            self.stats.loop_unrolls += 1
            current.store[s.index] = T.IConst(32, i)
            states = self._exec_block(s.body, states)
        return states

    def _symbolic_bound_loop(self, s: SLoop, st: SymState,
                             bound: T.ITerm) -> List[SymState]:
        variants: List[SymState] = []
        for n in range(s.max_bound + 1):
            pc = T.and_b([st.path, T.cmp_b("eq", bound, T.IConst(bound.bits, n)),
                          T.not_b(T.IsNullI(bound))])
            verdict = self._feasible(pc)
            if verdict == SolverResult.UNSAT:
                self.stats.pruned_branches += 1
                continue
            variant = self._clone(st, pc)
            for name, init in s.inits:
                variant.store[name] = self._eval_int(init, variant)
            variant.store[s.index] = T.IConst(32, 0)
            for i in range(n):
                self.stats.loop_unrolls += 1
                variant.store[s.index] = T.IConst(32, i)
                vs = self._exec_block(s.body, [variant])
                if not vs:
                    break
                variant = vs[0]
                if not variant.active:
                    break
            variants.append(variant)
        # Bound out of static range -> classified loop-bound failure.
        over_pc = T.and_b([
            st.path,
            T.or_b([T.IsNullI(bound),
                    T.cmp_b("gt", bound, T.IConst(bound.bits, s.max_bound)),
                    T.cmp_b("lt", bound, T.IConst(bound.bits, 0))]),
        ])
        if self._feasible(over_pc) != SolverResult.UNSAT:
            over = self._clone(st, over_pc)
            self._raise(over, E.E_LOOP_BOUND, s.loc)
            variants.append(over)
        else:
            self.stats.pruned_branches += 1
        # Return the disjoint variants as separate states; the driver's
        # cross-product comparison already handles multi-state summaries and
        # avoids the quadratic deep-ITE merge that a single state would need.
        return variants

    # ------------------------------------------------------------ evaluation
    def _eval_int(self, e: Expr, st: SymState) -> T.ITerm:
        if isinstance(e, EInt):
            return T.IConst(e.bits or 32, e.value)
        if isinstance(e, EField):
            return self._field_read(e, st)
        if isinstance(e, EVar):
            if e.name in st.store:
                return st.store[e.name]
            # Fall back to an input field of the same name.
            if e.name in self.rule.schema:
                return self._field_read(
                    EField(loc=e.loc, bits=e.bits, name=e.name), st)
            st.pending.append((T.TRUE, E.E_MISSING))
            return T.NullI(e.bits or 32)
        if isinstance(e, EWrap):
            inner = self._eval_int(e.arg, st)
            return T.wrap(inner, e.bits)
        if isinstance(e, EArith):
            return self._eval_arith(e, st)
        if isinstance(e, EIf):
            cond = self._eval_bool(e.cond, st)
            null_case = self._bool_is_null(cond)
            then_v = self._eval_int(e.then, st)
            else_v = self._eval_int(e.els, st)
            # NULL cond at expression level: classified failure, drained at the
            # next statement boundary.
            st.pending.append((null_case, E.E_NULL_BRANCH))
            return T.ite_i(T.and_b([T.not_b(null_case), cond]), then_v, else_v)
        if isinstance(e, EError):
            st.pending.append((T.TRUE, E.explicit_error(e.code)))
            return T.NullI(e.bits or 32)
        raise ValueError(f"symbolic engine cannot eval {type(e).__name__}")

    def _field_read(self, e: EField, st: SymState) -> T.ITerm:
        spec = self.rule.schema[e.name]
        # Missing field read is an immediate classified failure (pending until
        # the statement boundary, where the state is split).
        st.pending.append((T.not_b(T.Presence(e.name)), E.E_MISSING))
        # The value variable is valid when present & non-null; NULL is read
        # directly via IsNullI(IVar), which the solver resolves from the
        # field's null flag. No extra IteI(NullI) wrapper is needed.
        return T.IVar(spec.bits, e.name)

    def _eval_arith(self, e: EArith, st: SymState) -> T.ITerm:
        left = self._eval_int(e.left, st)
        right = self._eval_int(e.right, st)
        bits = e.bits
        null_input = T.or_b([T.IsNullI(left), T.IsNullI(right)])
        if e.op in ("add", "sub", "mul"):
            raw = T.bin_i(e.op, left, right, bits)
            # NULL propagates; otherwise wrap to width.
            return T.ite_i(null_input, T.NullI(bits), T.wrap(raw, bits))
        # div / rem: build explicit failure side conditions.
        min_int = -(1 << (bits - 1))
        div_zero = T.and_b([T.not_b(null_input),
                            T.cmp_b("eq", right, T.IConst(bits, 0))])
        trap = T.and_b([
            T.not_b(null_input),
            T.cmp_b("eq", left, T.IConst(bits, min_int)),
            T.cmp_b("eq", right, T.IConst(bits, -1)),
        ])
        fail = T.or_b([div_zero, trap])
        kind = E.E_DIV_TRAP  # exact kind selected below via ITE
        safe_right = T.ite_i(div_zero, T.IConst(bits, 1), right)
        safe_right = T.ite_i(trap, T.IConst(bits, 1), safe_right)
        raw = T.bin_i(e.op, left, safe_right, bits, fail=fail,
                      fail_kind=kind, fallback=T.IConst(bits, 0))
        result = T.ite_i(null_input, T.NullI(bits), T.wrap(raw, bits))
        # Record the two mutually exclusive failure classes in evaluation
        # order; the pending queue splits them at the statement boundary.
        st.pending.append((div_zero, E.E_DIV_ZERO))
        st.pending.append((trap, E.E_DIV_TRAP))
        return result

    def _eval_bool(self, e: Expr, st: SymState) -> T.BTerm:
        if isinstance(e, EMissing):
            return T.not_b(T.Presence(e.name))
        if isinstance(e, ENullTest):
            return T.IsNullI(self._eval_int(e.ref, st))
        if isinstance(e, ENot):
            return T.not_b(self._eval_bool(e.arg, st))
        if isinstance(e, EAnd):
            return T.and_b([self._eval_bool(x, st) for x in e.items])
        if isinstance(e, EOr):
            return T.or_b([self._eval_bool(x, st) for x in e.items])
        if isinstance(e, ECmp):
            left = self._eval_int(e.left, st)
            right = self._eval_int(e.right, st)
            base = T.cmp_b(e.op, left, right)
            # NULL operand -> three-valued NULL comparison.
            null_input = T.or_b([T.IsNullI(left), T.IsNullI(right)])
            return T.ite_b(null_input, T.BNULL, base)
        if isinstance(e, EIf):
            cond = self._eval_bool(e.cond, st)
            then_b = self._eval_bool(e.then, st)
            else_b = self._eval_bool(e.els, st)
            null_case = self._bool_is_null(cond)
            return T.ite_b(T.and_b([T.not_b(null_case), cond]), then_b, else_b)
        if isinstance(e, EError):
            self._raise(st, E.explicit_error(e.code), e.loc)
            return T.FALSE
        if isinstance(e, EInt):
            return T.BConst(bool(e.value))
        raise ValueError(f"symbolic engine cannot eval bool {type(e).__name__}")

    @staticmethod
    def _bool_is_null(b: T.BTerm) -> T.BTerm:
        """Boolean predicate: the three-valued term ``b`` evaluates to NULL."""
        if isinstance(b, T.BConst):
            return T.TRUE if b.value is None else T.FALSE
        if isinstance(b, (T.Presence, _NullFlag, _IsNullB, _NullAny)):
            return T.FALSE
        if isinstance(b, T.NotB):
            return SymbolicEngine._bool_is_null(b.arg)
        if isinstance(b, (T.AndB, T.OrB)):
            return _NullAny(tuple(b.items))
        if isinstance(b, T.CmpB):
            return _IsNullB(b)
        if isinstance(b, T.IteB):
            return _IsNullB(b)
        return _IsNullB(b)

    # ---------------------------------------------------------------- solver
    def _feasible(self, pc: T.BTerm) -> str:
        rep = self.solver.check(pc)
        self.stats.solver_calls += 1
        return rep.result

    @staticmethod
    def _clone(st: SymState, path: T.BTerm) -> SymState:
        return SymState(path=path, store=dict(st.store),
                        outputs=dict(st.outputs), raised=st.raised,
                        code_id=st.code_id, active=st.active, depth=st.depth,
                        pending=list(st.pending))
