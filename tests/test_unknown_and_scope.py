"""Tests for the honest out-of-scope / budget-exhausted behaviour."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etleq import EquivalenceChecker, parse_case
from etleq.parser import UnsupportedRule
from etleq.solver.native import NativeSolver

CASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "fixtures", "cases")


def load(name):
    with open(os.path.join(CASE_DIR, name)) as fh:
        return json.load(fh)


class TestOutOfScope(unittest.TestCase):
    def test_unknown_vocabulary_raises_unsupported(self):
        doc = {
            "input": {"x": {"bits": 8, "signed": True,
                            "nullable": False, "optional": False}},
            "old": {"output": {"y": {"bits": 8, "nullable": False}},
                    "block": [{"emit": {"output": "y",
                                        "value": {"pow": [{"field": "x"},
                                                          {"integer": 2}]}}}]},
            "new": {"output": {"y": {"bits": 8, "nullable": False}},
                    "block": [{"emit": {"output": "y", "value": {"integer": 0}}}]},
        }
        with self.assertRaises(UnsupportedRule):
            parse_case(doc)

    def test_budget_exhaustion_returns_unknown_not_guess(self):
        # Large-domain case forced onto the symbolic path, then starved so a
        # feasibility query cannot finish: the verdict must be "unknown".
        doc = load("multi-branch.json")
        old, new = parse_case(doc)
        checker = EquivalenceChecker(
            old, new,
            lambda s: NativeSolver(s, branch_budget=5),
            enumeration_cap=100,
        )
        verdict = checker.run()
        self.assertEqual(verdict.status, "unknown")
        self.assertIsNotNone(verdict.reason)


if __name__ == "__main__":
    unittest.main()
