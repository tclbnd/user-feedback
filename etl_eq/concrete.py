"""Concrete interpreter -- the semantic oracle.

This module is deliberately independent of the symbolic engine: it imports
only the node shapes from :mod:`etl_eq.rules` and the semantic primitives from
:mod:`etl_eq.semantics`.  Acceptance tests replay every symbolic witness
through this interpreter and require exact agreement.

Three-valued logic
------------------
Booleans are ``True`` / ``False`` / ``None``, where ``None`` means UNKNOWN
(SQL NULL propagated through a predicate).  A strict ``if`` takes its then
branch only when the condition is exactly ``True``; UNKNOWN and ``False``
both go to else.  ``tag`` collapses UNKNOWN/MISSING/NULL to ``False`` so that
``tite`` gives an explicit marker-preserving branch idiom.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from .rules import Rule
from .semantics import (
    MISSING,
    NULL,
    Outcome,
    RuleFailure,
)

UNKNOWN = None  # three-valued boolean UNKNOWN


def _is_marker(v: Any) -> bool:
    return v is MISSING or v is NULL


class ConcreteInterpreter:
    def __init__(self, rule: Rule):
        self.rule = rule

    def run(self, record: Mapping[str, Any]) -> Outcome:
        env: Dict[str, Any] = dict(record)
        emitted: List[Tuple[str, Any]] = []
        try:
            self._exec_block(self.rule.statements, env, emitted)
        except RuleFailure as fail:
            return Outcome.failure(fail.code, fail.location, emitted)
        return Outcome.success(emitted)

    # -- statements -------------------------------------------------------
    def _exec_block(self, stmts, env: Dict[str, Any], emitted) -> None:
        for st in stmts:
            kind = st[0]
            if kind == "assign":
                env[st[1]] = self.eval(st[2], env)
            elif kind == "emit":
                _, name, expr, loc = st
                try:
                    value = self.eval(expr, env)
                except RuleFailure as fail:
                    if fail.location.startswith("<"):
                        raise RuleFailure(fail.code, loc) from fail
                    raise
                emitted.append((name, value))
            elif kind == "error":
                raise RuleFailure(st[1], st[2])
            elif kind == "if":
                _, cond, then, other, loc = st
                if self.eval(cond, env) is True:
                    self._exec_block(then, env, emitted)
                else:
                    self._exec_block(other, env, emitted)
            elif kind == "loop":
                _, var, start, times, body, _loc = st
                for k in range(times):
                    env[var] = start + k
                    self._exec_block(body, env, emitted)
            else:  # pragma: no cover - guarded by parser
                raise AssertionError(f"unknown statement {kind}")

    # -- expressions ------------------------------------------------------
    def eval(self, node: tuple, env: Mapping[str, Any]) -> Any:
        tag = node[0]
        if tag == "lit":
            return node[1]
        if tag == "var":
            name = node[1]
            if name not in env:
                return MISSING
            v = env[name]
            return NULL if v is None else v
        if tag == "width":
            v = self.eval(node[2], env)
            if _is_marker(v):
                return v
            return node[1].apply_overflow(v)
        if tag == "neg":
            v = self.eval(node[2], env)
            if _is_marker(v):
                return v
            return node[1].apply_overflow(-v)
        if tag == "arith":
            return self._arith(node, env)
        if tag == "cmp":
            return self._compare(node, env)
        if tag == "is_missing":
            return env.get(node[1], MISSING) is MISSING
        if tag == "is_null":
            v = env.get(node[1], MISSING)
            return v is NULL or v is None
        if tag == "coalesce":
            v = self.eval(node[1], env)
            return v if not _is_marker(v) else self.eval(node[2], env)
        if tag == "fill_missing":
            v = self.eval(node[1], env)
            return self.eval(node[2], env) if v is MISSING else v
        if tag == "logic":
            return self._logic(node, env)
        if tag == "not":
            v = self.eval(node[1], env)
            return None if v is None else not v
        if tag == "ite":
            return self._ite(node, env, marker_preserving=False)
        if tag == "tite":
            return self._ite(node, env, marker_preserving=True)
        if tag == "tag":
            v = self.eval(node[1], env)
            return isinstance(v, int) and not isinstance(v, bool)
        if tag == "teq":
            a, b = self.eval(node[1], env), self.eval(node[2], env)
            if _is_marker(a) or _is_marker(b):
                return a is b
            return a == b
        if tag == "err-expr":
            raise RuleFailure(node[1], "<expr>")
        raise AssertionError(f"unknown expression {tag}")  # pragma: no cover

    def _arith(self, node: tuple, env: Mapping[str, Any]) -> Any:
        _, op, rtype, left_n, right_n = node
        a, b = self.eval(left_n, env), self.eval(right_n, env)
        if _is_marker(a) or _is_marker(b):
            # marker propagation: MISSING wins over NULL
            if a is MISSING or b is MISSING:
                return MISSING
            return NULL
        if op == "add":
            return rtype.apply_overflow(a + b)
        if op == "sub":
            return rtype.apply_overflow(a - b)
        if op == "mul":
            return rtype.apply_overflow(a * b)
        if op == "div":
            if b == 0:
                raise RuleFailure("div-by-zero", "<div>")
            if rtype.signed and a == rtype.min and b == -1:
                return rtype.apply_overflow(a)  # triggers overflow policy
            return rtype.apply_overflow(int(a / b) if a * b < 0 else a // b)
        if op == "rem":
            if b == 0:
                raise RuleFailure("div-by-zero", "<rem>")
            # truncation toward zero, remainder follows the dividend sign
            q = int(a / b) if a * b < 0 else a // b
            return rtype.apply_overflow(a - q * b)
        raise AssertionError(op)  # pragma: no cover

    def _compare(self, node: tuple, env: Mapping[str, Any]) -> Any:
        _, op, left_n, right_n = node
        a, b = self.eval(left_n, env), self.eval(right_n, env)
        if _is_marker(a) or _is_marker(b):
            return UNKNOWN
        ops = {
            "eq": lambda: a == b,
            "ne": lambda: a != b,
            "lt": lambda: a < b,
            "le": lambda: a <= b,
            "gt": lambda: a > b,
            "ge": lambda: a >= b,
        }
        return bool(ops[op]())

    def _logic(self, node: tuple, env: Mapping[str, Any]) -> Any:
        _, op, a_n, b_n = node
        a, b = self.eval(a_n, env), self.eval(b_n, env)
        if a not in (True, False, None) or b not in (True, False, None):
            raise RuleFailure("type-error", "<logic>")
        if op == "and":
            if a is False or b is False:
                return False
            if a is None or b is None:
                return None
            return True
        # or
        if a is True or b is True:
            return True
        if a is None or b is None:
            return None
        return False

    def _ite(self, node: tuple, env: Mapping[str, Any], marker_preserving: bool) -> Any:
        _, cond_n, then_n, else_n = node
        cond = self.eval(cond_n, env)
        if marker_preserving:
            take_then = cond is True
        else:
            take_then = cond is True  # strict: UNKNOWN/False both go else
        branch = then_n if take_then else else_n
        if branch[0] == "err-expr":
            raise RuleFailure(branch[1], "<if-expr>")
        return self.eval(branch, env)


def run_rule(rule: Rule, record: Mapping[str, Any]) -> Outcome:
    """Convenience entry point used by acceptance tests and the CLI."""
    return ConcreteInterpreter(rule).run(record)
