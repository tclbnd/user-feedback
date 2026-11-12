"""Symbolic term language for the finite ETL semantics.

Two sorts of terms exist:

* :class:`ITerm` -- machine integers of a fixed bit width plus an explicit
  NULL marker.  Missing fields are *not* a value sort: they are encoded with a
  separate boolean presence variable (see :mod:`etleq.solver.interface`).
* :class:`BTerm` -- three-valued booleans (TRUE / FALSE / NULL).

All terms are immutable and hashable so they can be used in sets, memo tables
and canonicalised reports.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Union


# ---------------------------------------------------------------------------
# Integer terms
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ITerm:
    bits: int

    # convenience constructors -------------------------------------------
    def is_null(self) -> "BTerm":
        return IsNullI(self)

    def is_not_null(self) -> "BTerm":
        return NotB(IsNullI(self))


@dataclass(frozen=True)
class IConst(ITerm):
    value: int


@dataclass(frozen=True)
class IVar(ITerm):
    """Input field value variable. Valid when the presence flag is true."""
    name: str


@dataclass(frozen=True)
class IteI(ITerm):
    """if-then-else; cond must be a definite (non-NULL) boolean."""
    cond: "BTerm"
    yes: ITerm
    no: ITerm


@dataclass(frozen=True)
class BinI(ITerm):
    """add/sub/mul/div/rem. Division/remainder are total: on the failure
    side-condition the term takes the value of ``fallback`` (0) while the
    companion boolean term records the failure classification."""
    op: str
    left: ITerm
    right: ITerm
    fail: "BTerm"          # true iff this operation raised E_DIV_ZERO / E_DIV_TRAP
    fail_kind: str         # which classification (only meaningful for div/rem)
    fallback: ITerm


@dataclass(frozen=True)
class WrapI(ITerm):
    """Reduce an unbounded integer term to two's complement of width ``bits``."""
    arg: ITerm


@dataclass(frozen=True)
class NullI(ITerm):
    """The NULL marker of this width."""


# ---------------------------------------------------------------------------
# Boolean terms (three valued)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BTerm:
    pass


@dataclass(frozen=True)
class BConst(BTerm):
    # True, False, or None meaning SQL NULL
    value: "bool | None"


TRUE = BConst(True)
FALSE = BConst(False)
BNULL = BConst(None)


@dataclass(frozen=True)
class IsNullI(BTerm):
    arg: ITerm


@dataclass(frozen=True)
class NotB(BTerm):
    arg: BTerm


@dataclass(frozen=True)
class AndB(BTerm):
    items: Tuple[BTerm, ...]


@dataclass(frozen=True)
class OrB(BTerm):
    items: Tuple[BTerm, ...]


@dataclass(frozen=True)
class CmpB(BTerm):
    """eq/ne/lt/le/gt/ge. Yields NULL when either operand is NULL."""
    op: str
    left: ITerm
    right: ITerm


@dataclass(frozen=True)
class Presence(BTerm):
    """True iff input field ``name`` is present (not MISSING)."""
    name: str


@dataclass(frozen=True)
class IteB(BTerm):
    cond: BTerm
    yes: BTerm
    no: BTerm


# ---------------------------------------------------------------------------
# Constructors with light constant folding
# ---------------------------------------------------------------------------

def iconst(value: int, bits: int) -> ITerm:
    return IConst(bits, value)


def wrap(arg: ITerm, bits: int) -> ITerm:
    if isinstance(arg, IConst):
        return IConst(bits, _twos_comp(arg.value, bits))
    if isinstance(arg, WrapI) and arg.bits == bits:
        return arg
    return WrapI(bits, arg)


def _twos_comp(value: int, bits: int) -> int:
    modulus = 1 << bits
    v = value % modulus
    if v >= 1 << (bits - 1):
        v -= modulus
    return v


def ite_i(cond: BTerm, yes: ITerm, no: ITerm) -> ITerm:
    if isinstance(cond, BConst):
        if cond.value is True:
            return yes
        if cond.value is False:
            return no
        # NULL condition: both branches undefined as values; caller must have
        # raised E_NULL_BRANCH before reaching here. Keep a neutral encoding.
        return NullI(yes.bits)
    if yes == no:
        return yes
    return IteI(yes.bits, cond, yes, no)


def ite_b(cond: BTerm, yes: BTerm, no: BTerm) -> BTerm:
    if isinstance(cond, BConst):
        return yes if cond.value is True else (no if cond.value is False else BNULL)
    if yes == no:
        return yes
    return IteB(cond, yes, no)


def not_b(arg: BTerm) -> BTerm:
    if isinstance(arg, BConst):
        return BCONST_TABLE[arg.value]
    if isinstance(arg, NotB):
        return arg.arg
    return NotB(arg)


BCONST_TABLE = {True: FALSE, False: TRUE, None: BNULL}


def and_b(items) -> BTerm:
    flat: list = []
    for it in items:
        if isinstance(it, BConst):
            if it.value is False:
                return FALSE
            if it.value is None:
                # NULL AND x: need the rest to decide; remember via ite later.
                return _null_and(flat, list(items)[list(items).index(it) + 1:])
            continue  # TRUE is neutral
        flat.append(it)
    if not flat:
        return TRUE
    if len(flat) == 1:
        return flat[0]
    return AndB(tuple(flat))


def _null_and(before, after) -> BTerm:
    rest = and_b([*before, *after])
    if isinstance(rest, BConst) and rest.value is False:
        return FALSE
    # NULL AND TRUE -> NULL ; NULL AND FALSE -> FALSE (handled above)
    return ite_b(rest, BNULL, FALSE)


def or_b(items) -> BTerm:
    flat: list = []
    for it in items:
        if isinstance(it, BConst):
            if it.value is True:
                return TRUE
            if it.value is None:
                return _null_or(flat, list(items)[list(items).index(it) + 1:])
            continue
        flat.append(it)
    if not flat:
        return FALSE
    if len(flat) == 1:
        return flat[0]
    return OrB(tuple(flat))


def _null_or(before, after) -> BTerm:
    rest = or_b([*before, *after])
    if isinstance(rest, BConst) and rest.value is True:
        return TRUE
    return ite_b(rest, TRUE, BNULL)


def cmp_b(op: str, left: ITerm, right: ITerm) -> BTerm:
    if isinstance(left, IConst) and isinstance(right, IConst):
        lv, rv = left.value, right.value
        table = {
            "eq": lv == rv, "ne": lv != rv,
            "lt": lv < rv, "le": lv <= rv,
            "gt": lv > rv, "ge": lv >= rv,
        }
        return BConst(table[op])
    return CmpB(op, left, right)


def bin_i(op: str, left: ITerm, right: ITerm, bits: int,
          fail: BTerm = FALSE, fail_kind: str = "",
          fallback: "ITerm | None" = None) -> ITerm:
    if fallback is None:
        fallback = IConst(bits, 0)
    if (isinstance(left, IConst) and isinstance(right, IConst)
            and isinstance(fail, BConst) and fail.value is not True):
        lv, rv = left.value, right.value
        if op == "add":
            val = lv + rv
        elif op == "sub":
            val = lv - rv
        elif op == "mul":
            val = lv * rv
        elif op == "div":
            val = _trunc_div(lv, rv)
        elif op == "rem":
            val = _trunc_rem(lv, rv)
        else:
            raise ValueError(f"unknown op {op}")
        return IConst(bits, _twos_comp(val, bits))
    return BinI(bits, op, left, right, fail, fail_kind, fallback)


def _trunc_div(a: int, b: int) -> int:
    # Python // floors; ETL division truncates toward zero.
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _trunc_rem(a: int, b: int) -> int:
    r = abs(a) % abs(b)
    return r if a >= 0 else -r


AnyTerm = Union[ITerm, BTerm]


# ---------------------------------------------------------------------------
# Term walking helpers
# ---------------------------------------------------------------------------

def i_subterms(t: ITerm):
    if isinstance(t, IteI):
        yield t.yes
        yield t.no
    elif isinstance(t, BinI):
        yield t.left
        yield t.right
        yield t.fallback
    elif isinstance(t, WrapI):
        yield t.arg


def b_subterms(t: BTerm):
    if isinstance(t, NotB):
        yield t.arg
    elif isinstance(t, (AndB, OrB)):
        yield from t.items
    elif isinstance(t, CmpB):
        return
    elif isinstance(t, IteB):
        yield t.yes
        yield t.no


def i_bool_subterms(t: ITerm):
    if isinstance(t, IteI):
        yield t.cond
    elif isinstance(t, BinI):
        yield t.fail


def b_int_subterms(t: BTerm):
    if isinstance(t, CmpB):
        yield t.left
        yield t.right
    elif isinstance(t, IteB):
        yield from ()
