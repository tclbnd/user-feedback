"""Independent concrete interpreter for the finite ETL semantics.

This module deliberately shares nothing with the symbolic engine except the
parsed AST and the error-code constants: every evaluation rule below is
implemented directly against plain Python values, so it can independently
replay witness inputs and validate symbolic conclusions.

Value model
-----------
* ``MISSING`` -- the input field was absent (only inputs can be missing);
* ``None``    -- SQL NULL;
* ``int``     -- a machine integer of the field's width (always kept wrapped).

Failure is modelled with :class:`RuleFailure`, carrying the classification,
the AST location that raised it and the branch trace taken so far.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import errors as E
from .ast import (
    EAnd, EArith, ECmp, EError, EField, EIf, EInt, EMissing, ENot, ENullTest,
    EOr, EVar, EWrap, FieldSpec, OutputSpec, Rule, SIf, SLet, SLoop, SSet,
    SEmit, SFail, Stmt, Expr,
)

MISSING = object()


@dataclass
class RuleFailure(Exception):
    code: str
    at: str
    trace: List[dict] = field(default_factory=list)
    iteration: Optional[int] = None


def twos_comp(value: int, bits: int) -> int:
    modulus = 1 << bits
    v = value % modulus
    if v >= 1 << (bits - 1):
        v -= modulus
    return v


def trunc_div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def trunc_rem(a: int, b: int) -> int:
    r = abs(a) % abs(b)
    return r if a >= 0 else -r


@dataclass
class ConcreteResult:
    # ordered: output name -> ("value", int) | ("null",) | ("missing",)
    outcomes: Dict[str, Tuple[str, Any]]
    error: Optional[dict]      # None, or {code, at, iteration}
    branch_trace: List[dict]

    def to_json(self) -> dict:
        return {
            "outcomes": {
                name: ({"kind": kind, "value": val} if kind == "value"
                       else {"kind": kind})
                for name, (kind, val) in self.outcomes.items()
            },
            "error": self.error,
            "branch_trace": list(self.branch_trace),
        }


class Interpreter:
    def __init__(self, rule: Rule):
        self.rule = rule
        self.schema: Dict[str, FieldSpec] = rule.schema
        self.output_specs: Dict[str, OutputSpec] = {o.name: o for o in rule.outputs}
        self.trace: List[dict] = []
        self.locals: Dict[str, Any] = {}
        self.outcomes: Dict[str, Tuple[str, Any]] = {}

    # ------------------------------------------------------------------ run
    def run(self, record: dict) -> ConcreteResult:
        self.trace = []
        self.locals = {}
        self.outcomes = {o.name: ("missing", None) for o in self.rule.outputs}
        error = None
        try:
            self._exec_block(self.rule.block)
        except RuleFailure as f:
            error = {"code": f.code, "at": f.at, "iteration": f.iteration}
        return ConcreteResult(outcomes=dict(self.outcomes), error=error,
                              branch_trace=list(self.trace))

    # ------------------------------------------------------------ statements
    def _exec_block(self, stmts: Tuple[Stmt, ...]):
        for s in stmts:
            self._exec(s)

    def _exec(self, s: Stmt):
        if isinstance(s, SLet):
            self.locals[s.name] = self._eval(s.value)
            return
        if isinstance(s, SSet):
            self.locals[s.name] = self._eval(s.value)
            return
        if isinstance(s, SFail):
            raise RuleFailure(E.explicit_error(s.code), s.loc, list(self.trace))
        if isinstance(s, SEmit):
            value = self._eval(s.value)
            spec = self.output_specs[s.output]
            if value is None and not spec.nullable:
                raise RuleFailure(E.E_NULL_OUTPUT, s.loc, list(self.trace))
            if value is None:
                self.outcomes[s.output] = ("null", None)
            elif value is MISSING:
                # Reaching an emit of a missing value means an earlier read
                # would already have failed; keep the defensive classification.
                raise RuleFailure(E.E_MISSING, s.loc, list(self.trace))
            else:
                self.outcomes[s.output] = ("value", twos_comp(int(value), spec.bits))
            return
        if isinstance(s, SIf):
            cond = self._eval(s.cond)
            if cond is None:
                raise RuleFailure(E.E_NULL_BRANCH, s.loc, list(self.trace))
            if cond is MISSING:
                raise RuleFailure(E.E_MISSING, s.loc, list(self.trace))
            self.trace.append({"loc": s.loc, "taken": "then" if cond else "else"})
            self._exec_block(s.then if cond else s.els)
            return
        if isinstance(s, SLoop):
            self._exec_loop(s)
            return
        raise RuleFailure(E.E_MISSING, s.loc, list(self.trace))  # pragma: no cover

    def _exec_loop(self, s: SLoop):
        bound = self._eval(s.bound)
        if bound is None:
            raise RuleFailure(E.E_NULL, s.loc, list(self.trace))
        if bound is MISSING:
            raise RuleFailure(E.E_MISSING, s.loc, list(self.trace))
        if not isinstance(bound, int) or bound < 0:
            raise RuleFailure(E.E_LOOP_BOUND, s.loc, list(self.trace))
        if bound > s.max_bound:
            raise RuleFailure(E.E_LOOP_BOUND, s.loc, list(self.trace),
                              iteration=bound)
        for name, init in s.inits:
            self.locals[name] = self._eval(init)
        for i in range(bound):
            self.locals[s.index] = i
            self.trace.append({"loc": s.loc, "iteration": i})
            try:
                self._exec_block(s.body)
            except RuleFailure as f:
                if f.iteration is None:
                    f.iteration = i
                raise

    # ----------------------------------------------------------- expressions
    def _eval(self, e: Expr):
        if isinstance(e, EInt):
            return e.value
        if isinstance(e, EField):
            if e.name not in self._record:
                raise RuleFailure(E.E_MISSING, e.loc, list(self.trace))
            v = self._record[e.name]
            if v is None:
                return None
            return twos_comp(int(v), e.bits)
        if isinstance(e, EVar):
            # Local first; fall back to an input field of the same name so a
            # {"var": "k"} can read the record directly before any let.
            if e.name in self.locals:
                return self.locals[e.name]
            if e.name in self._record:
                v = self._record[e.name]
                if v is None:
                    return None
                spec = self.schema.get(e.name)
                return twos_comp(int(v), spec.bits if spec else (e.bits or 32))
            raise RuleFailure(E.E_MISSING, e.loc, list(self.trace))
        if isinstance(e, EMissing):
            return e.name not in self._record
        if isinstance(e, ENullTest):
            v = self._eval(e.ref)
            if v is MISSING:
                raise RuleFailure(E.E_MISSING, e.loc, list(self.trace))
            return v is None
        if isinstance(e, ENot):
            v = self._eval(e.arg)
            return None if v is None else (not v)
        if isinstance(e, EAnd):
            saw_null = False
            for item in e.items:
                v = self._eval(item)
                if v is False:
                    return False
                if v is None:
                    saw_null = True
                elif v is not True:
                    raise RuleFailure(E.E_NULL_BRANCH, e.loc, list(self.trace))
            return None if saw_null else True
        if isinstance(e, EOr):
            saw_null = False
            for item in e.items:
                v = self._eval(item)
                if v is True:
                    return True
                if v is None:
                    saw_null = True
                elif v is not False:
                    raise RuleFailure(E.E_NULL_BRANCH, e.loc, list(self.trace))
            return None if saw_null else False
        if isinstance(e, ECmp):
            l = self._eval(e.left)
            r = self._eval(e.right)
            if l is None or r is None:
                return None
            return self._compare(e.op, l, r)
        if isinstance(e, EArith):
            return self._eval_arith(e)
        if isinstance(e, EWrap):
            v = self._eval(e.arg)
            return None if v is None else twos_comp(int(v), e.bits)
        if isinstance(e, EIf):
            cond = self._eval(e.cond)
            if cond is None:
                raise RuleFailure(E.E_NULL_BRANCH, e.loc, list(self.trace))
            if cond is MISSING:
                raise RuleFailure(E.E_MISSING, e.loc, list(self.trace))
            self.trace.append({"loc": e.loc, "taken": "then" if cond else "else"})
            return self._eval(e.then if cond else e.els)
        if isinstance(e, EError):
            raise RuleFailure(E.explicit_error(e.code), e.loc, list(self.trace))
        raise RuleFailure(E.E_MISSING, getattr(e, "loc", "?"), list(self.trace))  # pragma: no cover

    def _eval_arith(self, e: EArith):
        l = self._eval(e.left)
        r = self._eval(e.right)
        if l is None or r is None:
            return None
        bits = e.bits
        if e.op in ("add", "sub", "mul"):
            raw = {"add": l + r, "sub": l - r, "mul": l * r}[e.op]
            return twos_comp(raw, bits)
        # div / rem are partial: classify the two failure modes precisely.
        min_int = -(1 << (bits - 1))
        if r == 0:
            raise RuleFailure(E.E_DIV_ZERO, e.loc, list(self.trace))
        if l == min_int and r == -1:
            raise RuleFailure(E.E_DIV_TRAP, e.loc, list(self.trace))
        if e.op == "div":
            return twos_comp(trunc_div(l, r), bits)
        return twos_comp(trunc_rem(l, r), bits)

    @staticmethod
    def _compare(op: str, l: int, r: int) -> bool:
        return {
            "eq": l == r, "ne": l != r,
            "lt": l < r, "le": l <= r,
            "gt": l > r, "ge": l >= r,
        }[op]

    _record: dict = {}

    def bind(self, record: dict) -> "Interpreter":
        self._record = record
        return self


def interpret(rule: Rule, record: dict) -> ConcreteResult:
    """Convenience entry point: run ``rule`` on one concrete record."""
    return Interpreter(rule).bind(record).run(record)
