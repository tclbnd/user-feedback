"""etleq -- symbolic equivalence checking for finite ETL field rules."""
from .check import EquivalenceChecker, FieldDiff, Verdict
from .domain import enumerate_domain
from .interpreter import ConcreteResult, interpret
from .parser import RuleFormatError, UnsupportedRule, parse_case, parse_rule
from .solver import NativeSolver, build_solver

__all__ = [
    "EquivalenceChecker", "FieldDiff", "Verdict",
    "enumerate_domain", "ConcreteResult", "interpret",
    "RuleFormatError", "UnsupportedRule", "parse_case", "parse_rule",
    "NativeSolver", "build_solver",
]
