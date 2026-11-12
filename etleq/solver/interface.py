"""Solver abstraction and domain model.

Each input field ``f`` with a FieldSpec is modelled by three variables:

* ``pres[f]`` boolean -- the field is present (FALSE means *missing*);
* ``null[f]`` boolean -- when present, the field is SQL NULL;
* ``val[f]``  integer -- when present and non-null, its machine value.

Domain axioms (built once per check)::

    null[f]  => pres[f]
    val[f] in [domain_lo, domain_hi]

so the three-level lattice is exact: missing / present-null / present-value.

An *outcome* (the observable result attached to one output, or to the rule's
error slot) is the triple ``(presence, null, value)`` plus, for the error
slot, a small integer encoding the classification.  Equivalence of two
outcomes means all three components match (and the error codes match).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .. import terms as T
from ..ast import FieldSpec, Rule
from ..errors import E_MISSING, E_NULL, ERROR_ORDER, error_rank, is_explicit


# ---------------------------------------------------------------------------
# Outcome encoding
# ---------------------------------------------------------------------------

# Outcome kinds, in the canonical comparison order (missing < null < value;
# error slot uses the same encoding with its own explicit ordering rule).
O_MISSING = 0
O_NULL = 1
O_VALUE = 2


@dataclass(frozen=True)
class Outcome:
    """Symbolic observable result of one output slot."""
    bits: int
    present: T.BTerm       # FALSE -> output slot never assigned / rule failed first
    isnull: T.BTerm        # TRUE -> present NULL
    value: T.ITerm         # machine value when present & non-null

    def matches(self, other: "Outcome") -> T.BTerm:
        return T.and_b([
            T.ite_b(self.present, other.present, T.not_b(other.present)),
            T.ite_b(self.isnull, other.isnull, T.not_b(other.isnull)),
            T.cmp_b("eq", self.value, other.value),
        ])


@dataclass(frozen=True)
class ErrorOutcome:
    """Symbolic rule-level failure slot.

    ``raised`` says whether any failure occurred; ``code_id`` is an integer
    identifier of the classification (see :func:`code_to_id`).
    """
    raised: T.BTerm
    code_id: T.ITerm
    codes: Tuple[str, ...]

    def matches(self, other: "ErrorOutcome") -> T.BTerm:
        both_down = T.and_b([T.not_b(self.raised), T.not_b(other.raised)])
        both_up = T.and_b([self.raised, other.raised,
                           T.cmp_b("eq", self.code_id, other.code_id)])
        return T.or_b([both_down, both_up])


def all_codes(rules: Tuple[Rule, ...]) -> Tuple[str, ...]:
    """Collect every error class reachable in either version, stably sorted."""
    found = set()

    def walk(node):
        # imported here to avoid a cycle at module import
        from .. import ast as A
        if isinstance(node, A.EError):
            found.add("E_EXPLICIT:" + node.code)
        elif isinstance(node, A.SFail):
            found.add("E_EXPLICIT:" + node.code)
        for attr in ("items",):
            if hasattr(node, attr):
                for child in getattr(node, attr):
                    walk(child)
        for attr in ("left", "right", "arg", "ref", "cond", "then", "els",
                     "value", "bound", "body", "block"):
            v = getattr(node, attr, None)
            if isinstance(v, tuple):
                for child in v:
                    walk(child)
            elif v is not None and hasattr(v, "loc"):
                walk(v)
        if hasattr(node, "inits"):
            for _, child in node.inits:
                walk(child)

    for r in rules:
        walk(r)
    for c in ERROR_ORDER:
        if not is_explicit(c):
            found.add(c)
    return tuple(sorted(found, key=lambda c: (error_rank(c), c)))


def code_to_id(code: str, codes: Tuple[str, ...]) -> int:
    return codes.index(code)


# ---------------------------------------------------------------------------
# Model values (plain Python, solver independent)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InputPoint:
    """One concrete point of the joint input domain."""
    values: Dict[str, Optional[int]]   # name -> int | None (NULL) ; missing keys absent
    missing: Tuple[str, ...]

    def key(self) -> tuple:
        return tuple((n, self.values.get(n, MISSING_TOKEN))
                     for n in sorted(self.values) + list(self.missing))

    def to_json(self) -> dict:
        out = {}
        for name in self.missing:
            out[name] = {"kind": "missing"}
        for name, v in self.values.items():
            out[name] = {"kind": "null"} if v is None else {"kind": "value", "value": v}
        return out


class _Missing:
    def __repr__(self):
        return "MISSING"


MISSING_TOKEN = _Missing()


def lex_less(a: InputPoint, b: InputPoint, schema: Dict[str, FieldSpec]) -> bool:
    """Total order on input points; shared by minimization and report output."""
    for name in sorted(schema):
        ra = _rank(a, name)
        rb = _rank(b, name)
        if ra != rb:
            return ra < rb
        va = a.values.get(name)
        vb = b.values.get(name)
        if isinstance(va, int) and isinstance(vb, int) and va != vb:
            return va < vb
    return False


def _rank(p: InputPoint, name: str) -> int:
    if name in p.missing:
        return 0
    if p.values.get(name) is None:
        return 1
    return 2


# ---------------------------------------------------------------------------
# Solver interface
# ---------------------------------------------------------------------------

class SolverResult:
    SAT = "sat"
    UNSAT = "unsat"
    UNKNOWN = "unknown"


@dataclass
class CheckReport:
    result: str
    model: Optional[InputPoint] = None
    calls: int = 0
    budget_exhausted: bool = False
    pruned: int = 0


class Solver:
    """Minimal SMT-style interface used by the symbolic engine."""

    name = "abstract"

    def __init__(self, schema: Dict[str, FieldSpec], *, branch_budget: int = 200_000):
        self.schema = schema
        self.branch_budget = branch_budget
        self.calls = 0
        self.pruned = 0
        self.codes: Tuple[str, ...] = ()

    # term variables -------------------------------------------------------
    def pres(self, name: str) -> T.BTerm:
        return T.Presence(name)

    def null_flag(self, name: str) -> T.BTerm:
        raise NotImplementedError

    def value_var(self, name: str) -> T.ITerm:
        return T.IVar(self.schema[name].bits, name)

    def domain_axioms(self) -> T.BTerm:
        items = []
        for name, spec in self.schema.items():
            # null => present
            items.append(T.or_b([T.not_b(self.null_flag(name)), self.pres(name)]))
        return T.and_b(items)

    # queries --------------------------------------------------------------
    def check(self, cond: T.BTerm, *, minimize: bool = False) -> CheckReport:
        """Decide satisfiability of ``domain_axioms & cond``.

        When ``minimize`` is true the returned model is the lexicographically
        smallest satisfying point (stable witness order).
        """
        raise NotImplementedError

    def check_with_model(self, cond: T.BTerm) -> CheckReport:
        return self.check(cond, minimize=False)

    # statistics -----------------------------------------------------------
    def stats(self) -> dict:
        return {"solver": self.name, "calls": self.calls, "pruned": self.pruned}
