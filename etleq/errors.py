"""Stable error classification for the finite ETL semantics.

These constants are the only observable failure classes.  They are intentionally
flat strings so reports are stable across runs and across both interpreters.
"""

# Field was absent from the input record (distinct from SQL NULL).
E_MISSING = "E_MISSING"
# Field was present but explicitly NULL.
E_NULL = "E_NULL"
# A conditional's tested value was NULL (no branch is defined for it).
E_NULL_BRANCH = "E_NULL_BRANCH"
# Integer division / remainder with a zero divisor.
E_DIV_ZERO = "E_DIV_ZERO"
# INT_MIN / -1 or INT_MIN % -1: the true quotient cannot be represented.
E_DIV_TRAP = "E_DIV_TRAP"
# A value assigned to a non-nullable output turned out to be NULL.
E_NULL_OUTPUT = "E_NULL_OUTPUT"
# A loop body was reached more times than the configured static bound.
E_LOOP_BOUND = "E_LOOP_BOUND"
# A rule reached an explicit (error "...") form.
E_EXPLICIT = "E_EXPLICIT:"

# Canonical order used whenever error classes are sorted for stable output.
ERROR_ORDER = [
    E_MISSING,
    E_NULL,
    E_NULL_BRANCH,
    E_DIV_ZERO,
    E_DIV_TRAP,
    E_NULL_OUTPUT,
    E_LOOP_BOUND,
    E_EXPLICIT,
]


def explicit_error(name: str) -> str:
    """Canonical spelling of an explicit ``(error "name")`` classification."""
    return E_EXPLICIT + name


def is_explicit(code: str) -> bool:
    return code.startswith(E_EXPLICIT)


def error_rank(code: str) -> int:
    """Total order on error codes; explicit errors share one rank and sort by text."""
    if is_explicit(code):
        return len(ERROR_ORDER) - 1
    return ERROR_ORDER.index(code)
