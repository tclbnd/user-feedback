"""Parsed rule AST.

Two surface forms compile into this AST (see :mod:`etleq.parser`):

1. expression form -- every output is directly an expression, as in the
   original ``fixtures/price-rules.json``;
2. statement form -- an ordered block of ``let / if / loop / emit / fail``
   statements, needed for statically bounded loops.

Every node carries a stable ``loc`` id assigned in document preorder so that
both interpreters and the symbolic engine can report identical branch
positions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

ARITH_OPS = ("add", "sub", "mul", "div", "rem")
CMP_OPS = ("eq", "ne", "lt", "le", "gt", "ge")


@dataclass(frozen=True)
class FieldSpec:
    bits: int
    signed: bool
    nullable: bool
    optional: bool

    def domain_lo(self) -> int:
        if self.signed:
            return -(1 << (self.bits - 1))
        return 0

    def domain_hi(self) -> int:
        if self.signed:
            return (1 << (self.bits - 1)) - 1
        return (1 << self.bits) - 1


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Expr:
    loc: str
    bits: Optional[int]  # filled by the width-resolution pass (None on bools)


@dataclass(frozen=True)
class EInt(Expr):
    value: int


@dataclass(frozen=True)
class EField(Expr):
    name: str


@dataclass(frozen=True)
class EVar(Expr):
    name: str


@dataclass(frozen=True)
class EMissing(Expr):
    """is_missing: tests presence of an *input* field."""
    name: str


@dataclass(frozen=True)
class ENullTest(Expr):
    """is_null: tests NULL on a field or local variable reference."""
    ref: Expr


@dataclass(frozen=True)
class ENot(Expr):
    arg: Expr


@dataclass(frozen=True)
class EAnd(Expr):
    items: Tuple[Expr, ...]


@dataclass(frozen=True)
class EOr(Expr):
    items: Tuple[Expr, ...]


@dataclass(frozen=True)
class ECmp(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class EArith(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class EWrap(Expr):
    arg: Expr


@dataclass(frozen=True)
class EIf(Expr):
    cond: Expr
    then: Expr
    els: Expr


@dataclass(frozen=True)
class EError(Expr):
    code: str  # explicit error classification suffix


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stmt:
    loc: str


@dataclass(frozen=True)
class SLet(Stmt):
    name: str
    value: Expr


@dataclass(frozen=True)
class SSet(Stmt):
    name: str
    value: Expr


@dataclass(frozen=True)
class SIf(Stmt):
    cond: Expr
    then: Tuple[Stmt, ...]
    els: Tuple[Stmt, ...]


@dataclass(frozen=True)
class SLoop(Stmt):
    bound: Expr            # evaluated once at entry; must be 0..max_bound
    max_bound: int         # static unrolling ceiling
    index: str
    inits: Tuple[Tuple[str, Expr], ...]
    body: Tuple[Stmt, ...]


@dataclass(frozen=True)
class SEmit(Stmt):
    output: str
    value: Expr


@dataclass(frozen=True)
class SFail(Stmt):
    code: str


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutputSpec:
    name: str
    bits: int
    nullable: bool


@dataclass(frozen=True)
class Rule:
    schema: dict                      # name -> FieldSpec
    outputs: Tuple[OutputSpec, ...]
    block: Tuple[Stmt, ...]
    loop_bound: int                   # global static unrolling budget
