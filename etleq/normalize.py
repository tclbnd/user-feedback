"""Semantics-preserving term normalisation before solving.

The symbolic engine builds terms by path-indexed ITE merging, which is
faithful but verbose. This module rewrites those terms into a shape the ICP
solver handles directly, without changing their meaning:

* ``IteI(c, NullI, x)`` keeps a plain value ``x`` and records its NULL
  predicate separately (NULL is already observable via :class:`IsNullI`);
* ``IteI(c, x, x)`` collapses to ``x``;
* ``IteB(c, TRUE, FALSE)`` becomes the condition ``c`` itself;
* constant branches over a constant condition fold away;
* ``IsNullI`` over nested ITEs is pushed to the leaf variables, where it maps
  onto input null flags;
* double negations and neutral AND/OR items are removed.

The rewrite is bottom-up and memoised, so deep merge trees are flattened in
linear time. It never invents constraints; it only removes redundant ITE
scaffolding around the same underlying variables.
"""
from __future__ import annotations

from dataclasses import fields as dc_fields
from typing import Dict, List

from . import terms as T


def _struct_key(t) -> tuple:
    """Structural key for hash-consing, independent of object identity."""
    parts = []
    for f in dc_fields(t):
        v = getattr(t, f.name)
        if isinstance(v, (T.ITerm, T.BTerm)):
            parts.append((f.name, id(v)))  # children already interned
        elif isinstance(v, tuple):
            parts.append((f.name, tuple(id(x) if isinstance(
                x, (T.ITerm, T.BTerm)) else x for x in v)))
        else:
            parts.append((f.name, v))
    return tuple(parts)


class Normalizer:
    def __init__(self):
        self._icache: Dict[int, T.ITerm] = {}
        self._bcache: Dict[int, T.BTerm] = {}
        # Hash-consing pools: structurally identical terms collapse to one
        # object, so the solver's id-keyed interval/flag caches are shared and
        # two equal subexpressions cannot drift into different intervals.
        self._ipool: Dict[tuple, T.ITerm] = {}
        self._bpool: Dict[tuple, T.BTerm] = {}

    def _intern_b(self, t: T.BTerm) -> T.BTerm:
        # Structural equality fallback: dataclass equality is order-sensitive
        # but correct for fully-normalised children, so two equal normal
        # forms collapse even if their object ids differ.
        for existing in self._bpool.values():
            if existing == t:
                return existing
        key = ("b", type(t).__name__, _struct_key(t))
        self._bpool[key] = t
        return t

    def _intern_i(self, t: T.ITerm) -> T.ITerm:
        for existing in self._ipool.values():
            if existing == t:
                return existing
        key = ("i", type(t).__name__, getattr(t, "bits", None),
               _struct_key(t))
        self._ipool[key] = t
        return t

    # -------------------------------------------------------------- integers
    def norm_i(self, t: T.ITerm) -> T.ITerm:
        key = id(t)
        if key in self._icache:
            return self._icache[key]
        out = self._intern_i(self._norm_i(t))
        self._icache[key] = out
        return out

    def _norm_i(self, t: T.ITerm) -> T.ITerm:
        if isinstance(t, (T.IConst, T.NullI, T.IVar)):
            return t
        if isinstance(t, T.WrapI):
            return T.wrap(self.norm_i(t.arg), t.bits)
        if isinstance(t, T.IteI):
            cond = self.norm_b(t.cond)
            yes = self.norm_i(t.yes)
            no = self.norm_i(t.no)
            if isinstance(cond, T.BConst):
                if cond.value is True:
                    return yes
                if cond.value is False:
                    return no
            if yes == no:
                return yes
            # Keep the IteI structure when a branch is NULL: the condition is
            # semantically load-bearing for IsNullI and for value selection on
            # that partition. Dropping it would erase the NULL/missing path.
            return T.IteI(t.bits, cond, yes, no)
        if isinstance(t, T.BinI):
            left = self.norm_i(t.left)
            right = self.norm_i(t.right)
            fallback = self.norm_i(t.fallback)
            fail = self.norm_b(t.fail)
            return T.BinI(t.bits, t.op, left, right, fail, t.fail_kind, fallback)
        return t

    # -------------------------------------------------------------- booleans
    def norm_b(self, t: T.BTerm) -> T.BTerm:
        key = id(t)
        if key in self._bcache:
            return self._bcache[key]
        out = self._intern_b(self._norm_b(t))
        self._bcache[key] = out
        return out

    def _norm_b(self, t: T.BTerm) -> T.BTerm:
        if isinstance(t, T.BConst):
            return t
        if isinstance(t, (T.Presence,)):
            return t
        if isinstance(t, T.IsNullI):
            return self._norm_isnull(t.arg)
        if isinstance(t, T.NotB):
            inner = self.norm_b(t.arg)
            # Double negation and De Morgan's laws push negation to literals.
            if isinstance(inner, T.NotB):
                return inner.arg
            if isinstance(inner, T.AndB):
                return self._fold_or([self.norm_b(T.not_b(x)) for x in inner.items])
            if isinstance(inner, T.OrB):
                return self._fold_and([self.norm_b(T.not_b(x)) for x in inner.items])
            if isinstance(inner, T.BConst):
                return T.TRUE if inner.value is False else T.FALSE
            return T.not_b(inner)
        if isinstance(t, T.AndB):
            items = self._canonical_assoc(T.AndB, [self.norm_b(x) for x in t.items])
            return self._fold_and(items)
        if isinstance(t, T.OrB):
            items = self._canonical_assoc(T.OrB, [self.norm_b(x) for x in t.items])
            return self._fold_or(items)
        if isinstance(t, T.CmpB):
            left = self.norm_i(t.left)
            right = self.norm_i(t.right)
            return self._norm_cmp(t.op, left, right)
        if isinstance(t, T.IteB):
            cond = self.norm_b(t.cond)
            yes = self.norm_b(t.yes)
            no = self.norm_b(t.no)
            if isinstance(cond, T.BConst):
                return yes if cond.value is True else no
            if yes == no:
                return yes
            if isinstance(yes, T.BConst) and isinstance(no, T.BConst):
                if yes.value is True and no.value is False:
                    return cond
                if yes.value is False and no.value is True:
                    return T.not_b(cond)
            # Boolean algebra identities over the branch condition c:
            #   if c then FALSE else not c  == FALSE
            #   if c then TRUE  else not c  == TRUE
            #   if c then c      else x       == c OR x
            #   if c then x      else c       == c AND x
            neg_cond = T.not_b(cond)
            if no == neg_cond:
                if isinstance(yes, T.BConst) and yes.value is False:
                    return T.FALSE
                if isinstance(yes, T.BConst) and yes.value is True:
                    return T.TRUE
            if yes == cond:
                return T.or_b([cond, no])
            if no == cond:
                return T.and_b([cond, yes])
            # Common-conjunct reduction: cond = (c AND g...) and
            # no = NOT(NOT c AND g...) share the guard conjunction g. Under g,
            # the term behaves as (if c then yes else not c), which collapses
            # to a constant when yes itself is that constant.
            reduced = self._reduce_common_guard(cond, yes, no)
            if reduced is not None:
                return reduced
            return T.IteB(cond, yes, no)
        # Solver-local term types (_NullFlag / SymIsNullB / SymNullAny) keep
        # their structure; their inner terms are still normalised.
        if hasattr(t, "arg") and isinstance(getattr(t, "arg"), T.BTerm):
            return type(t)(self.norm_b(t.arg))
        if hasattr(t, "items"):
            return type(t)(tuple(self.norm_b(x) for x in t.items))
        if hasattr(t, "name"):
            return t
        return t

    def _reduce_common_guard(self, cond: T.BTerm, yes: T.BTerm,
                             no: T.BTerm):
        if not isinstance(cond, T.AndB) or not isinstance(no, T.NotB) \
                or not isinstance(no.arg, T.AndB):
            return None
        cond_parts = list(cond.items)
        no_parts = list(no.arg.items)
        # The negated branch must be a conjunction of (NOT c_i, guards...).
        # Pair up: find a literal in cond whose negation appears in no, while
        # all remaining parts are shared guards.
        cond_set = set(cond_parts)
        no_set = set(no_parts)
        pivot = None
        for cp in cond_parts:
            neg = T.not_b(cp)
            if neg in no_parts:
                pivot = cp
                break
        if pivot is None:
            return None
        shared_cond = [p for p in cond_parts if p is not pivot]
        shared_no = [p for p in no_parts if p != T.not_b(pivot)]
        if set(shared_cond) != set(shared_no):
            return None
        # Under the shared guard g: term = (if pivot then yes else not pivot).
        if isinstance(yes, T.BConst) and yes.value is False:
            # (if p then FALSE else ¬p) is FALSE whenever defined; guard g
            # already guarantees the three-valued condition is decided.
            return T.FALSE
        if isinstance(yes, T.BConst) and yes.value is True:
            return T.TRUE
        return None

    def _norm_cmp(self, op: str, left: T.ITerm, right: T.ITerm) -> T.BTerm:
        # Wrap semantics are handled by the solver's modular interval
        # contractor; keep comparisons intact so no order/congruence
        # information is lost here.
        return T.cmp_b(op, left, right)

    @staticmethod
    def _fold_and(items: List[T.BTerm]) -> T.BTerm:
        if any(isinstance(x, T.BConst) and x.value is False for x in items):
            return T.FALSE
        kept = [x for x in items if not (isinstance(x, T.BConst) and x.value is True)]
        # Complementary literals make the conjunction unsatisfiable.
        for x in kept:
            if isinstance(x, T.NotB) and x.arg in kept:
                return T.FALSE
        if not kept:
            return T.TRUE
        return T.and_b(kept)

    @staticmethod
    def _fold_or(items: List[T.BTerm]) -> T.BTerm:
        if any(isinstance(x, T.BConst) and x.value is True for x in items):
            return T.TRUE
        kept = [x for x in items if not (isinstance(x, T.BConst) and x.value is False)]
        for x in kept:
            if isinstance(x, T.NotB) and x.arg in kept:
                return T.TRUE
        if not kept:
            return T.FALSE
        return T.or_b(kept)

    @staticmethod
    def _canonical_assoc(kind, items: List[T.BTerm]) -> List[T.BTerm]:
        """Flatten nested same-kind connectives and sort children by a stable
        structural key so (A AND B) and (B AND A) normalise identically."""
        flat: List[T.BTerm] = []

        def walk(node: T.BTerm):
            if isinstance(node, kind):
                for child in node.items:
                    walk(child)
            else:
                flat.append(node)

        for it in items:
            walk(it)
        # Deduplicate (idempotence) then order deterministically.
        uniq: List[T.BTerm] = []
        for it in flat:
            if it not in uniq:
                uniq.append(it)
        return sorted(uniq, key=lambda b: repr(b))

    def _norm_isnull(self, arg: T.ITerm) -> T.BTerm:
        arg = self.norm_i(arg)
        if isinstance(arg, T.NullI):
            return T.TRUE
        if isinstance(arg, (T.IConst,)):
            return T.FALSE
        if isinstance(arg, T.IVar):
            return T.IsNullI(arg)
        if isinstance(arg, T.IteI):
            # IsNullI(if c then y else n)  ==  if c then IsNullI(y)
            #                                     else IsNullI(n)
            return T.ite_b(arg.cond, self._norm_isnull(arg.yes),
                           self._norm_isnull(arg.no))
        if isinstance(arg, T.WrapI):
            return self._norm_isnull(arg.arg)
        if isinstance(arg, T.BinI):
            return T.or_b([self._norm_isnull(arg.left),
                           self._norm_isnull(arg.right)])
        return T.IsNullI(arg)


def normalize(term: T.BTerm) -> T.BTerm:
    return Normalizer().norm_b(term)


def normalize_i(term: T.ITerm) -> T.ITerm:
    return Normalizer().norm_i(term)
