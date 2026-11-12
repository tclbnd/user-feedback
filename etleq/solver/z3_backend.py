"""Optional z3 backend.

This backend is used only when ``z3-solver`` is installed and the caller asks
for it (``build_solver(..., prefer_z3=True)``).  The term translation mirrors
the finite semantics exactly, including three-valued booleans encoded as an
integer sort (0=FALSE, 1=TRUE, 2=NULL) and the two division failure classes.
"""
from __future__ import annotations

from typing import Dict, Optional

from .. import terms as T
from ..ast import FieldSpec
from ..errors import E_DIV_TRAP, E_DIV_ZERO
from .interface import CheckReport, InputPoint, Solver, SolverResult
from .native import _NullFlag

B_FALSE, B_TRUE, B_NULL = 0, 1, 2


class Z3Solver(Solver):
    name = "z3"

    def __init__(self, schema: Dict[str, FieldSpec], *, branch_budget: int = 200_000):
        super().__init__(schema, branch_budget=branch_budget)
        import z3  # imported lazily so the package works without z3
        self.z3 = z3
        self.ctx = z3.Context()
        self._ivars: Dict[str, object] = {}
        self._pres: Dict[str, object] = {}
        self._nflags: Dict[str, object] = {}
        self._icache: Dict[int, object] = {}
        self._bcache: Dict[int, object] = {}
        self._build_vars()

    # ------------------------------------------------------------- variables
    def _build_vars(self):
        z3 = self.z3
        for name, spec in self.schema.items():
            iv = z3.Int(f"v_{name}", ctx=self.ctx)
            pv = z3.Bool(f"p_{name}", ctx=self.ctx)
            nv = z3.Bool(f"n_{name}", ctx=self.ctx)
            self._ivars[name] = iv
            self._pres[name] = pv
            self._nflags[name] = nv
            self.solver = z3.Solver(ctx=self.ctx)
            self.solver.add(z3.Implies(nv, pv))
            self.solver.add(iv >= spec.domain_lo(), iv <= spec.domain_hi())

    def null_flag(self, name: str) -> T.BTerm:
        return _NullFlag(name)

    # --------------------------------------------------------- translation
    def _b(self, t: T.BTerm):
        key = id(t)
        if key in self._bcache:
            return self._bcache[key]
        z3 = self.z3
        if isinstance(t, T.BConst):
            v = {True: B_TRUE, False: B_FALSE, None: B_NULL}[t.value]
            out = z3.IntVal(v, ctx=self.ctx)
        elif isinstance(t, T.Presence):
            out = z3.If(self._pres[t.name], B_TRUE, B_FALSE, ctx=self.ctx)
        elif isinstance(t, _NullFlag):
            out = z3.If(self._nflags[t.name], B_TRUE, B_FALSE, ctx=self.ctx)
        elif isinstance(t, T.NotB):
            inner = self._b(t.arg)
            out = z3.If(inner == B_NULL, B_NULL,
                        z3.If(inner == B_TRUE, B_FALSE, B_TRUE, ctx=self.ctx),
                        ctx=self.ctx)
        elif isinstance(t, T.AndB):
            out = self._b(t.items[0])
            for it in t.items[1:]:
                out = self._and3(out, self._b(it))
        elif isinstance(t, T.OrB):
            out = self._b(t.items[0])
            for it in t.items[1:]:
                out = self._or3(out, self._b(it))
        elif isinstance(t, T.CmpB):
            out = self._cmp(t)
        elif isinstance(t, T.IsNullI):
            iv = self._i(t.arg)
            out = z3.If(iv is None, B_TRUE, B_FALSE, ctx=self.ctx) \
                if iv is None else z3.If(self._is_null_expr(t.arg), B_TRUE, B_FALSE,
                                         ctx=self.ctx)
        elif isinstance(t, T.IteB):
            cond = self._b(t.cond)
            out = z3.If(cond == B_TRUE, self._b(t.yes), self._b(t.no), ctx=self.ctx)
        else:
            raise TypeError(f"z3 backend cannot translate {type(t).__name__}")
        self._bcache[key] = out
        return out

    def _and3(self, a, b):
        z3 = self.z3
        return z3.If(z3.Or(a == B_FALSE, b == B_FALSE, ctx=self.ctx), B_FALSE,
                     z3.If(z3.And(a == B_NULL, b == B_NULL, ctx=self.ctx), B_NULL,
                           z3.If(z3.Or(a == B_NULL, b == B_NULL, ctx=self.ctx),
                                 B_NULL, B_TRUE, ctx=self.ctx), ctx=self.ctx),
                     ctx=self.ctx)

    def _or3(self, a, b):
        z3 = self.z3
        return z3.If(z3.Or(a == B_TRUE, b == B_TRUE, ctx=self.ctx), B_TRUE,
                     z3.If(z3.Or(a == B_NULL, b == B_NULL, ctx=self.ctx), B_NULL,
                           B_FALSE, ctx=self.ctx), ctx=self.ctx)

    def _cmp(self, t: T.CmpB):
        z3 = self.z3
        l, r = self._i(t.left), self._i(t.right)
        null = z3.Or(self._is_null_expr(t.left), self._is_null_expr(t.right),
                     ctx=self.ctx)
        op = {"eq": l == r, "ne": l != r, "lt": l < r, "le": l <= r,
              "gt": l > r, "ge": l >= r}[t.op]
        return z3.If(null, B_NULL, z3.If(op, B_TRUE, B_FALSE, ctx=self.ctx),
                     ctx=self.ctx)

    def _i(self, t: T.ITerm):
        key = id(t)
        if key in self._icache:
            return self._icache[key]
        z3 = self.z3
        if isinstance(t, T.IConst):
            out = z3.IntVal(t.value, ctx=self.ctx)
        elif isinstance(t, T.NullI):
            out = None
        elif isinstance(t, T.IVar):
            out = self._ivars[t.name]
        elif isinstance(t, T.WrapI):
            out = self._wrap(self._i(t.arg), t.bits)
        elif isinstance(t, T.IteI):
            cond = self._b(t.cond)
            out = z3.If(cond == B_TRUE, self._i(t.yes), self._i(t.no), ctx=self.ctx)
        elif isinstance(t, T.BinI):
            out = self._bin(t)
        else:
            raise TypeError(f"z3 backend cannot translate {type(t).__name__}")
        if out is not None:
            self._icache[key] = out
        return out

    def _is_null_expr(self, t: T.ITerm):
        z3 = self.z3
        if isinstance(t, T.NullI):
            return z3.BoolVal(True, ctx=self.ctx)
        if isinstance(t, T.IVar):
            return z3.And(self._pres[t.name], self._nflags[t.name], ctx=self.ctx)
        if isinstance(t, T.IConst):
            return z3.BoolVal(False, ctx=self.ctx)
        if isinstance(t, T.IteI):
            cond = self._b(t.cond)
            return z3.If(cond == B_TRUE, self._is_null_expr(t.yes),
                         self._is_null_expr(t.no), ctx=self.ctx)
        if isinstance(t, T.BinI):
            return z3.Or(self._is_null_expr(t.left), self._is_null_expr(t.right),
                         ctx=self.ctx)
        if isinstance(t, T.WrapI):
            return self._is_null_expr(t.arg)
        return z3.BoolVal(False, ctx=self.ctx)

    def _bin(self, t: T.BinI):
        z3 = self.z3
        l, r = self._i(t.left), self._i(t.right)
        if t.op == "add":
            return self._wrap(l + r, t.bits)
        if t.op == "sub":
            return self._wrap(l - r, t.bits)
        if t.op == "mul":
            return self._wrap(l * r, t.bits)
        fail = self._b(t.fail) == B_TRUE
        if t.op == "div":
            safe = z3.If(fail, z3.IntVal(1, ctx=self.ctx), r, ctx=self.ctx)
            return self._wrap(z3.If(safe == 0, 0, l / safe, ctx=self.ctx), t.bits)
        safe = z3.If(fail, z3.IntVal(1, ctx=self.ctx), r, ctx=self.ctx)
        return self._wrap(z3.If(safe == 0, 0, l % safe, ctx=self.ctx), t.bits)

    def _wrap(self, expr, bits: int):
        z3 = self.z3
        modulus = 1 << bits
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        wrapped = expr % modulus
        return z3.If(wrapped >= (1 << (bits - 1)), wrapped - modulus, wrapped,
                     ctx=self.ctx)

    # ------------------------------------------------------------------ API
    def check(self, cond: T.BTerm, *, minimize: bool = False) -> CheckReport:
        self.calls += 1
        z3 = self.z3
        s = z3.Solver(ctx=self.ctx)
        # Re-add domain axioms from the stored main solver.
        s.add(self.solver.assertions())
        s.add(self._b(cond) == B_TRUE)
        verdict = s.check()
        if verdict == z3.unsat:
            return CheckReport(SolverResult.UNSAT, calls=self.calls)
        if verdict == z3.unknown:
            return CheckReport(SolverResult.UNKNOWN, calls=self.calls,
                               budget_exhausted=True)
        model = s.model()
        values, missing = {}, []
        for name, spec in self.schema.items():
            if z3.is_false(model.eval(self._pres[name], model_completion=True)):
                missing.append(name)
            elif z3.is_true(model.eval(self._nflags[name], model_completion=True)):
                values[name] = None
            else:
                values[name] = model.eval(self._ivars[name],
                                          model_completion=True).as_long()
        point = InputPoint(values=values, missing=tuple(sorted(missing)))
        return CheckReport(SolverResult.SAT, model=point, calls=self.calls)
