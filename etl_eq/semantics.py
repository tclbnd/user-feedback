"""Finite semantic domain shared by every component.

The semantics deliberately distinguishes two kinds of "absence":

* MISSING -- the field key is not present in the source record at all.
* NULL    -- the field key is present but its value is SQL NULL.

Both propagate through ordinary arithmetic, but they are observable with
``is_missing`` / ``is_null`` and are classified separately by errors.

Every integer lives in a fixed-width type (8/16/32/64 bits, signed or
unsigned).  Arithmetic has three explicit overflow policies:

* ``wrap``    -- two's complement wrap (the default, like Java/SQL SMALLINT);
* ``saturate``-- clamp to the representable min/max;
* ``error``   -- overflow is raised as a classified rule error.

Failures are never exceptions that destroy the record: a rule either emits
field values or terminates at an ``error`` node carrying a classification
string.  Division by zero is likewise a classified error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple


class _Marker:
    """Singleton marker, compared by identity and rendered stably."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return hash(self.name)


MISSING = _Marker("MISSING")
NULL = _Marker("NULL")
MARKERS = (MISSING, NULL)


class RuleFailure(Exception):
    """Raised internally by the concrete interpreter when an ``error`` node fires."""

    def __init__(self, code: str, location: str):
        super().__init__(f"rule error {code!r} at {location}")
        self.code = code
        self.location = location


class UnsupportedRule(Exception):
    """Raised when a rule uses constructs outside the supported semantics."""


class BudgetExceeded(Exception):
    """Raised by the finite-domain solver when its search budget is exhausted."""


WIDTHS = (4, 8, 10, 12, 14, 16, 32, 64)


@dataclass(frozen=True)
class IntType:
    """A fixed-width integer type."""

    bits: int
    signed: bool
    overflow: str = "wrap"

    def __post_init__(self) -> None:
        if self.bits not in WIDTHS:
            raise UnsupportedRule(f"unsupported bit width {self.bits}")
        if self.overflow not in ("wrap", "saturate", "error"):
            raise UnsupportedRule(f"unsupported overflow policy {self.overflow!r}")

    @property
    def min(self) -> int:
        return -(2 ** (self.bits - 1)) if self.signed else 0

    @property
    def max(self) -> int:
        if self.signed:
            return 2 ** (self.bits - 1) - 1
        return 2**self.bits - 1

    @property
    def modulus(self) -> int:
        return 2**self.bits

    def wrap_value(self, x: int) -> int:
        """Two's-complement wrap of an arbitrary Python int into this type."""
        m = self.modulus
        y = x % m
        if self.signed and y >= m // 2:
            y -= m
        return y

    def fits(self, x: int) -> bool:
        return self.min <= x <= self.max

    def apply_overflow(self, x: int) -> int:
        """Apply the declared overflow policy to an unbounded result."""
        if self.fits(x):
            return x
        if self.overflow == "wrap":
            return self.wrap_value(x)
        if self.overflow == "saturate":
            return min(self.max, max(self.min, x))
        raise RuleFailure("overflow", "<arith>")

    def label(self) -> str:
        sign = "i" if self.signed else "u"
        return f"{sign}{self.bits}/{self.overflow}"


# ---------------------------------------------------------------------------
# Observable outcomes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    """The fully observable result of running one rule on one record.

    ``error`` is None on success; otherwise ``fields`` holds whatever was
    emitted before the failure (possibly empty) and ``error_location`` is the
    location of the ``error`` node that fired.
    """

    fields: Tuple[Tuple[str, Any], ...]
    error: Optional[str]
    error_location: Optional[str]

    @staticmethod
    def success(fields) -> "Outcome":
        return Outcome(tuple(sorted(fields)), None, None)

    @staticmethod
    def failure(code: str, location: str, fields) -> "Outcome":
        return Outcome(tuple(sorted(fields)), code, location)

    def as_jsonable(self) -> dict:
        return {
            "fields": [[name, _jsonable(v)] for name, v in self.fields],
            "error": self.error,
            "error_location": self.error_location,
        }


def _jsonable(v: Any) -> Any:
    if v is MISSING:
        return "MISSING"
    if v is NULL:
        return "NULL"
    return v


def same_observable(a: Any, b: Any) -> bool:
    """Equality on observable values (identity for markers)."""
    if a is MISSING or a is NULL or b is MISSING or b is NULL:
        return a is b
    return a == b


def outcomes_equal(a: Outcome, b: Outcome) -> bool:
    if a.error != b.error:
        return False
    if a.error_location != b.error_location:
        return False
    if len(a.fields) != len(b.fields):
        return False
    return all(
        fa[0] == fb[0] and same_observable(fa[1], fb[1])
        for fa, fb in zip(a.fields, b.fields)
    )


# Stable total order on domain values, used for minimal witnesses.
# MISSING < NULL < integers in numeric order.
def value_order_key(v: Any):
    if v is MISSING:
        return (0, 0)
    if v is NULL:
        return (1, 0)
    return (2, v)


# Classification catalog used for documentation and deterministic output.
ERROR_KINDS = (
    "overflow",       # fixed-width arithmetic overflow with policy=error
    "div-by-zero",    # integer division or remainder by zero
    "rule-error",     # explicit ``error`` node (business classification)
    "type-error",     # value used outside its domain (e.g. integer on NULL path)
)
