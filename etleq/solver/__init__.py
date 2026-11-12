"""Solver backends: native ICP solver (default) and an optional z3 adapter."""
from .interface import (
    CheckReport, InputPoint, Solver, SolverResult, lex_less,
)
from .native import NativeSolver

__all__ = [
    "CheckReport", "InputPoint", "Solver", "SolverResult",
    "NativeSolver", "lex_less", "build_solver",
]


def build_solver(schema, *, prefer_z3: bool = False, **kwargs):
    """Return a solver instance.

    Uses z3 when it is importable and ``prefer_z3`` is set; otherwise the
    dependency-free native solver is used.  The native solver is always the
    acceptance reference path on locked-down machines.
    """
    if prefer_z3:
        try:
            from .z3_backend import Z3Solver  # noqa: WPS433
            return Z3Solver(schema, **kwargs)
        except ImportError:
            pass
    return NativeSolver(schema, **kwargs)
