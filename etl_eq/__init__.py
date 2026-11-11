"""ETL rule symbolic equivalence checking toolkit.

Public surface:

* :mod:`etl_eq.semantics`   -- finite semantic domain (MISSING/NULL, widths)
* :mod:`etl_eq.rules`       -- rule format, parser, type inference
* :mod:`etl_eq.concrete`    -- independent concrete interpreter (oracle)
* :mod:`etl_eq.fdsolver`    -- pure-Python finite-domain constraint solver
* :mod:`etl_eq.symbolic`    -- symbolic execution with pruning and merging
* :mod:`etl_eq.equivalence` -- equivalence entry point and witness reports
* :mod:`etl_eq.exhaustive`  -- brute-force cross-check over a whole domain
"""

from .semantics import MISSING, NULL, Outcome, IntType, RuleFailure, UnsupportedRule
from .rules import parse_case, parse_rule, Rule, Schema, FieldSpec
from .concrete import run_rule, ConcreteInterpreter
from .equivalence import (
    EquivalenceChecker,
    check_equivalence,
    EquivalenceReport,
    Witness,
    EQUIVALENT,
    NOT_EQUIVALENT,
    UNDECIDABLE,
)

__all__ = [
    "MISSING",
    "NULL",
    "Outcome",
    "IntType",
    "RuleFailure",
    "UnsupportedRule",
    "parse_case",
    "parse_rule",
    "Rule",
    "Schema",
    "FieldSpec",
    "run_rule",
    "ConcreteInterpreter",
    "EquivalenceChecker",
    "check_equivalence",
    "EquivalenceReport",
    "Witness",
    "EQUIVALENT",
    "NOT_EQUIVALENT",
    "UNDECIDABLE",
]
