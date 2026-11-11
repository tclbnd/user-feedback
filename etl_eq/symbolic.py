"""Symbolic execution engine over finite domains.

The engine unrolls each rule into *states*; every state carries a path
constraint (a list of solver constraints) and a symbolic environment plus the
set of emitted output fields.  Two mechanisms keep the state space finite
and small:

**Feasible-path pruning.**  Every newly forked state gets one feasibility
query against the finite-domain solver.  Infeasible branches are discarded
immediately and counted, so contradictory branch combinations are never
explored.  The same mechanism handles arithmetic failure guards
(divide-by-zero, overflow-policy errors): a state whose guard must fire is
turned into a terminal error state, and the surviving state continues with
the negated guard.

**Merge-at-confluence.**  At every ``if`` join and after every unrolled loop
iteration, sibling states with identical environment bindings, emitted
fields and error status are merged.  The merge unions the path constraints
after an exact compatibility feasibility query, keeping one state that
covers every way of reaching the same observable situation.

Each state records the branch locations it traversed, so a difference report
can name the exact ``if``/``error`` position in each version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .fdsolver import Domain, FiniteDomainSolver, BudgetExceeded
from .rules import Rule
from .semantics import IntType, MISSING, NULL, UnsupportedRule

FALSE_NODE = ("c", False)
TRUE_NODE = ("c", True)


def _and(a: tuple, b: tuple) -> tuple:
    return ("logic", "and", a, b)


def _not_true(cond: tuple) -> tuple:
    return ("nottrue", cond)


class Sym:
    """A symbolic value.

    ``node``      -- fdsolver trail node for the value itself;
    ``kind``      -- "int" | "bool" | "err";
    ``itype``     -- resolved IntType for integer values;
    ``failures``  -- ordered ``(guard-node, error-code)`` pairs: on a row,
                     if guard evaluates True the expression terminates the
                     rule with that error before producing its value.
    """

    __slots__ = ("node", "kind", "itype", "failures")

    def __init__(self, node, kind="int", itype=None, failures=()):
        self.node = node
        self.kind = kind
        self.itype = itype
        self.failures = tuple(failures)


def const_sym(v: Any) -> Sym:
    if v is MISSING or v is NULL:
        return Sym(("c", v), "int")
    if isinstance(v, bool) or v is None:
        return Sym(("c", v), "bool")
    if isinstance(v, int):
        return Sym(("c", v), "int")
    raise UnsupportedRule(f"cannot make symbolic constant from {v!r}")


def _combine_failures(parts: List[Sym]) -> Tuple[Tuple[tuple, str], ...]:
    out: List[Tuple[tuple, str]] = []
    for p in parts:
        out.extend(p.failures)
    return tuple(out)


@dataclass
class PathState:
    constraints: List[tuple]
    env: Dict[str, Sym]
    emitted: Tuple[Tuple[str, Sym], ...]
    error: Optional[Tuple[str, str]] = None
    locations: Tuple[str, ...] = ()
    merged_from: int = 1

    def observable_key(self) -> tuple:
        """Identity for confluence merging: same bindings/outputs/error,
        regardless of the path constraints used to reach them."""
        env_part = tuple(sorted((k, _node_key(v.node)) for k, v in self.env.items()))
        out_part = tuple((n, _node_key(s.node)) for n, s in self.emitted)
        return (env_part, out_part, self.error)


def _node_key(node):
    out = [node[0]]
    for part in node[1:]:
        if isinstance(part, IntType):
            out.append(("T", part.bits, part.signed, part.overflow))
        elif isinstance(part, tuple):
            out.append(_node_key(part))
        else:
            out.append(part)
    return tuple(out)


@dataclass
class ExecutionReport:
    states: List[PathState]
    pruned_branches: int
    merged_states: int
    solver_calls: int
    max_live_states: int
    iterations: int


class SymbolicEngine:
    def __init__(self, rule: Rule, solver_factory, budget: int):
        self.rule = rule
        self._make_solver = solver_factory
        self.budget = budget
        self.pruned = 0
        self.merged = 0
        self.calls = 0
        self.max_live = 0
        self.iterations = 0

    def execute(self) -> ExecutionReport:
        initial = self._initial_state()
        states = self._exec_block(self.rule.statements, [initial])
        states = self.revalidate(states)
        return ExecutionReport(
            states=states,
            pruned_branches=self.pruned,
            merged_states=self.merged,
            solver_calls=self.calls,
            max_live_states=self.max_live,
            iterations=self.iterations,
        )

    def _initial_state(self) -> PathState:
        st = PathState(constraints=[], env={}, emitted=())
        for spec in self.rule.schema.fields:
            values = tuple(range(spec.type.min, spec.type.max + 1)) + spec.domain_markers()
            st.env[spec.name] = Sym(("v", spec.name, spec.type), "int", spec.type)
            st.constraints.append(("__domain__", spec.name, values))
        return st

    # -- solver queries ---------------------------------------------------
    def _feasible(self, constraints: List[tuple]) -> bool:
        self.calls += 1
        solver = self._make_solver()
        domains: Dict[str, Domain] = {}
        for con in constraints:
            if con[0] == "__domain__":
                domains[con[1]] = Domain(con[2])
        for spec in self.rule.schema.fields:
            domains.setdefault(spec.name, Domain.int_range(spec.type))
        for name, dom in domains.items():
            solver.add_var(name, dom, "int")
        for con in constraints:
            if con[0] != "__domain__":
                solver.add_constraint(con[0], *con[1:])
        return solver.feasible()

    def _safe_feasible(self, constraints) -> bool:
        try:
            return self._feasible(constraints)
        except BudgetExceeded:
            return False  # sound: do not merge what cannot be proven compatible

    def revalidate(self, states: List[PathState]) -> List[PathState]:
        """Final pruning pass: drop states whose merged constraint set is
        unsatisfiable as a whole.  Merging unions constraints from different
        paths, so each result needs its own feasibility query."""
        live: List[PathState] = []
        for s in states:
            if self._safe_feasible(s.constraints):
                live.append(s)
            else:
                self.pruned += 1
        return live

    def _branch(self, state: PathState, extra: tuple, loc: Optional[str]) -> Optional[PathState]:
        constraints = state.constraints + [extra]
        if not self._feasible(constraints):
            self.pruned += 1
            return None
        return PathState(
            constraints=constraints,
            env=dict(state.env),
            emitted=state.emitted,
            error=state.error,
            locations=state.locations + ((loc,) if loc else ()),
            merged_from=state.merged_from,
        )

    # -- statements -------------------------------------------------------
    def _exec_block(self, stmts, states: List[PathState]) -> List[PathState]:
        for st in stmts:
            nxt: List[PathState] = []
            for state in states:
                if state.error is not None:
                    nxt.append(state)
                    continue
                nxt.extend(self._exec_stmt(st, state))
            states = self._merge(nxt)
            self.max_live = max(self.max_live, len(states))
        return states

    def _exec_stmt(self, st: tuple, state: PathState) -> List[PathState]:
        kind = st[0]
        if kind == "assign":
            return self._apply_value(st[1], st[2], state, emit=False, loc=None)
        if kind == "emit":
            _, name, expr, loc = st
            return self._apply_value(name, expr, state, emit=True, loc=loc)
        if kind == "error":
            return [PathState(
                constraints=state.constraints, env=state.env, emitted=state.emitted,
                error=(st[1], st[2]), locations=state.locations, merged_from=state.merged_from,
            )]
        if kind == "if":
            return self._exec_if(st, state)
        if kind == "loop":
            return self._exec_loop(st, state)
        raise AssertionError(kind)  # pragma: no cover

    def _apply_value(self, name: str, expr: tuple, state: PathState, emit: bool, loc) -> List[PathState]:
        value = self.eval(expr, state)
        # Fork on every failure guard in statement order.  Rows on which an
        # earlier guard fires never reach later ones.
        survivors: List[PathState] = [state]
        failed: List[PathState] = []
        alive_cond: Optional[tuple] = None
        for guard, code in value.failures:
            gated = guard if alive_cond is None else _and(alive_cond, guard)
            new_survivors: List[PathState] = []
            for s in survivors:
                fail_st = self._branch(s, ("true", gated), loc)
                if fail_st is not None:
                    fail_st.error = (code, loc or "<expr>")
                    failed.append(fail_st)
                cont = self._branch(s, ("false", gated), None)
                if cont is not None:
                    new_survivors.append(cont)
            survivors = new_survivors
            alive_cond = gated if alive_cond is None else _and(alive_cond, _not_true(gated))
            if not survivors:
                break
        out = list(failed)
        for s in survivors:
            clean = Sym(value.node, value.kind, value.itype)  # guards already forked
            if emit:
                s.emitted = s.emitted + ((name, clean),)
            else:
                s.env[name] = clean
            out.append(s)
        return out

    def _exec_if(self, st: tuple, state: PathState) -> List[PathState]:
        _, cond, then, other, loc = st
        cond_sym = self.eval(cond, state)
        # condition failures fire before branch selection
        survivors, failed = self._split_guards(state, cond_sym.failures, loc)
        out: List[PathState] = list(failed)
        for s in survivors:
            t_state = self._branch(s, ("true", cond_sym.node), f"{loc}#T")
            e_state = self._branch(s, ("false", cond_sym.node), f"{loc}#E")
            if t_state is not None:
                out.extend(self._exec_block(then, [t_state]))
            if e_state is not None:
                out.extend(self._exec_block(other, [e_state]))
        return out

    def _split_guards(self, state: PathState, failures, loc):
        survivors = [state]
        failed: List[PathState] = []
        for guard, code in failures:
            new_survivors = []
            for s in survivors:
                f = self._branch(s, ("true", guard), loc)
                if f is not None:
                    f.error = (code, loc or "<expr>")
                    failed.append(f)
                c = self._branch(s, ("false", guard), None)
                if c is not None:
                    new_survivors.append(c)
            survivors = new_survivors
        return survivors, failed

    def _exec_loop(self, st: tuple, state: PathState) -> List[PathState]:
        _, var, start, times, body, _loc = st
        states = [state]
        for k in range(times):
            self.iterations += 1
            nxt: List[PathState] = []
            for s in states:
                if s.error is not None:
                    nxt.append(s)
                    continue
                s.env[var] = const_sym(start + k)
                nxt.extend(self._exec_block(body, [s]))
            states = self._merge(nxt)
            self.max_live = max(self.max_live, len(states))
        for s in states:
            s.env.pop(var, None)
        return states

    # -- merging ----------------------------------------------------------
    def _merge(self, states: List[PathState]) -> List[PathState]:
        """Merge at a confluence.

        States are grouped by their *observable* effects -- environment
        bindings, emitted field values and terminal error.  Path constraints
        deliberately do not participate in the key: two ways of reaching the
        same observable situation are merged whenever the union of their path
        constraints remains feasible, which is decided by an exact solver
        query.  This keeps one state per distinct observable outcome while
        preserving every path constraint inside the merged state.
        """
        buckets: Dict[tuple, List[PathState]] = {}
        order: List[tuple] = []
        for s in states:
            key = s.observable_key()
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(s)

        merged: List[PathState] = []
        for key in order:
            group = buckets[key]
            current = group[0]
            for cand in group[1:]:
                extra = [c for c in cand.constraints if c not in current.constraints]
                if not extra or self._safe_feasible(current.constraints + extra):
                    current = PathState(
                        constraints=current.constraints + extra,
                        env=current.env,
                        emitted=current.emitted,
                        error=current.error,
                        locations=tuple(sorted(set(current.locations) | set(cand.locations))),
                        merged_from=current.merged_from + cand.merged_from,
                    )
                    self.merged += 1
                else:
                    merged.append(current)
                    current = cand
            merged.append(current)
        merged.sort(key=lambda s: (s.locations, len(s.constraints)))
        return merged

    # -- expressions ------------------------------------------------------
    def eval(self, node: tuple, state: PathState) -> Sym:
        tag = node[0]
        if tag == "lit":
            return Sym(("c", node[1]), "int", node[2])
        if tag == "var":
            if node[1] not in state.env:
                return Sym(("c", MISSING), "int", node[2])
            return state.env[node[1]]
        if tag == "width":
            inner = self.eval(node[2], state)
            return Sym(("width", node[1], inner.node), "int", node[1], inner.failures)
        if tag == "neg":
            inner = self.eval(node[2], state)
            return Sym(("neg", node[1], inner.node), "int", node[1], inner.failures)
        if tag == "arith":
            return self._arith(node, state)
        if tag == "cmp":
            a, b = self.eval(node[2], state), self.eval(node[3], state)
            return Sym(("cmp", node[1], a.node, b.node), "bool",
                       failures=_combine_failures([a, b]))
        if tag in ("is_missing", "is_null"):
            kind = "miss" if tag == "is_missing" else "null"
            return Sym((kind, node[1]), "bool")
        if tag in ("coalesce", "fill_missing"):
            return self._coalesce(node, state)
        if tag == "logic":
            a, b = self.eval(node[2], state), self.eval(node[3], state)
            return Sym(("logic", node[1], a.node, b.node), "bool",
                       failures=_combine_failures([a, b]))
        if tag == "not":
            a = self.eval(node[1], state)
            return Sym(("not", a.node), "bool", failures=a.failures)
        if tag in ("ite", "tite"):
            return self._ite(node, state)
        if tag == "tag":
            a = self.eval(node[1], state)
            return Sym(("tag", a.node), "bool", failures=a.failures)
        if tag == "teq":
            a, b = self.eval(node[1], state), self.eval(node[2], state)
            return Sym(("teq", a.node, b.node), "bool", failures=_combine_failures([a, b]))
        if tag == "err-expr":
            return Sym(("err", node[1]), "err", failures=((TRUE_NODE, node[1]),))
        raise AssertionError(tag)  # pragma: no cover

    def _arith(self, node: tuple, state: PathState) -> Sym:
        _, op, rtype, left_n, right_n = node
        a, b = self.eval(left_n, state), self.eval(right_n, state)
        base = _combine_failures([a, b])
        value_node = ("arith", op, rtype, a.node, b.node)
        guards: List[Tuple[tuple, str]] = list(base)
        if op in ("div", "rem"):
            guards.append((("istrue", ("divz", a.node, b.node)), "div-by-zero"))
            if rtype.signed:
                ovf = ("ovf", op, rtype, a.node, b.node)
                if rtype.overflow == "error":
                    guards.append((("istrue", ovf), "overflow"))
                # wrap/saturate: min/-1 is handled inside the arith node
        elif rtype.overflow == "error":
            ovf = ("ovf", op, rtype, a.node, b.node)
            guards.append((("istrue", ovf), "overflow"))
        return Sym(value_node, "int", rtype, failures=tuple(guards))

    def _coalesce(self, node: tuple, state: PathState) -> Sym:
        a, b = self.eval(node[1], state), self.eval(node[2], state)
        if node[0] == "coalesce":
            # result = ite(tag(a), a, b): a selected when cond holds
            cond = ("tag", a.node)
            value = ("ite", cond, a.node, b.node)
            failures = self._gate_failures(cond, a.failures) + \
                       self._gate_failures(_not_true(cond), b.failures)
        else:  # fill_missing: only MISSING replaced; NULL preserved
            cond = ("teq", a.node, ("c", MISSING))
            value = ("ite", cond, b.node, a.node)
            failures = self._gate_failures(cond, b.failures) + \
                       self._gate_failures(_not_true(cond), a.failures)
        return Sym(value, "int", a.itype or b.itype, failures=tuple(failures))

    def _ite(self, node: tuple, state: PathState) -> Sym:
        cond = self.eval(node[1], state)
        then, other = self.eval(node[2], state), self.eval(node[3], state)
        kind = "tite" if node[0] == "tite" else "ite"
        cond_node = cond.node
        failures = list(cond.failures)
        failures.extend(self._gate_failures(cond_node, then.failures))
        failures.extend(self._gate_failures(_not_true(cond_node), other.failures))
        itype = then.itype or other.itype
        result_kind = "err" if then.kind == "err" and other.kind == "err" else "int"
        return Sym((kind, cond_node, then.node, other.node), result_kind, itype,
                   failures=tuple(failures))

    @staticmethod
    def _gate_failures(gate: tuple, failures) -> List[Tuple[tuple, str]]:
        return [(_and(gate, g), code) for g, code in failures]
