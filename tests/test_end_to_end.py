"""End-to-end tests: parsing, concrete semantics, and equivalence verdicts."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etleq import EquivalenceChecker, build_solver, interpret, parse_case
from etleq.domain import domain_size, enumerate_domain, point_to_record

CASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "fixtures", "cases")
PRICE = os.path.join(os.path.dirname(CASE_DIR), "price-rules.json")


def load(name):
    path = name if os.path.isabs(name) else os.path.join(CASE_DIR, name)
    with open(path) as fh:
        return json.load(fh)


def observable(result):
    return {"outcomes": result["outcomes"],
            "error": (None if result["error"] is None
                      else {"code": result["error"]["code"],
                            "iteration": result["error"]["iteration"]})}


class TestConcreteSemantics(unittest.TestCase):
    def test_overflow_wraps(self):
        old, _ = parse_case(load("overflow-wrap.json"))
        r = interpret(old, {"a": 127}).to_json()
        self.assertEqual(r["outcomes"]["wrapped"]["value"], -128)

    def test_missing_vs_null_distinct(self):
        old, _ = parse_case(load("overflow-wrap.json"))
        self.assertEqual(interpret(old, {}).to_json()["error"]["code"], "E_MISSING")
        self.assertEqual(interpret(old, {"a": None}).to_json()
                         ["outcomes"]["wrapped"]["kind"], "null")

    def test_truncating_division(self):
        old, _ = parse_case(load("div-rem.json"))
        r = interpret(old, {"x": -7}).to_json()["outcomes"]
        self.assertEqual(r["q"]["value"], -2)
        self.assertEqual(r["r"]["value"], -1)

    def test_div_failure_classes(self):
        old, _ = parse_case(load("div-failures.json"))
        self.assertEqual(interpret(old, {"x": 5, "d": 0}).to_json()
                         ["error"]["code"], "E_EXPLICIT:divisor-zero")
        self.assertEqual(interpret(old, {"x": -128, "d": -1}).to_json()
                         ["error"]["code"], "E_EXPLICIT:divisor-trap")

    def test_loop_accumulation_and_bound(self):
        old, _ = parse_case(load("loop-sum.json"))
        self.assertEqual(interpret(old, {"k": 5}).to_json()
                         ["outcomes"]["total"]["value"], 10)
        err = interpret(old, {"k": 9}).to_json()["error"]
        self.assertEqual(err["code"], "E_LOOP_BOUND")


class TestEquivalenceVerdicts(unittest.TestCase):
    def _verdict(self, doc):
        old, new = parse_case(doc)
        return old, new, EquivalenceChecker(
            old, new, lambda s: build_solver(s)).run()

    def test_equivalent_cases(self):
        for name in ("overflow-wrap.json", "div-rem.json", "div-failures.json",
                     "multi-branch.json", "loop-sum.json"):
            with self.subTest(name=name):
                _, _, v = self._verdict(load(name))
                self.assertEqual(v.status, "equivalent", name)

    def test_price_equivalent_after_alignment(self):
        import copy
        doc = load(PRICE)
        aligned = copy.deepcopy(doc)
        aligned["new"]["output"]["adjusted"]["if"][2]["if"][1] = \
            {"error": "null-base"}
        _, _, v = self._verdict(aligned)
        self.assertEqual(v.status, "equivalent")

    def test_planted_price_difference_minimal_witness(self):
        _, _, v = self._verdict(load(PRICE))
        self.assertEqual(v.status, "different")
        # The classification difference is on the NULL input.
        self.assertEqual(v.witness.to_json(), {"base": {"kind": "null"}})

    def test_null_branch_boundary_witness(self):
        _, _, v = self._verdict(load("null-branch.json"))
        self.assertEqual(v.status, "different")
        # gt vs ge differ only at the boundary x = 0.
        self.assertEqual(v.witness.to_json(),
                         {"x": {"kind": "value", "value": 0}})

    def test_overflow_boundary_witness(self):
        _, _, v = self._verdict(load("overflow-diff.json"))
        self.assertEqual(v.status, "different")
        # First observable difference is the NULL input (missing is equal).
        self.assertEqual(v.witness.to_json(), {"a": {"kind": "null"}})

    def test_loop_last_iteration_witness(self):
        _, _, v = self._verdict(load("loop-last-iteration.json"))
        self.assertEqual(v.status, "different")
        self.assertEqual(v.witness.to_json(),
                         {"k": {"kind": "value", "value": 1}})


class TestExhaustiveAgreement(unittest.TestCase):
    """Symbolic/enumerative verdict must match a full concrete enumeration."""

    def test_small_domains_agree_with_oracle(self):
        for name in ("div-rem.json", "overflow-wrap.json", "overflow-diff.json",
                     "null-branch.json"):
            with self.subTest(name=name):
                old, new, v = self._load_verdict(name)
                oracle_equal = all(
                    observable(interpret(old, point_to_record(p)).to_json())
                    == observable(interpret(new, point_to_record(p)).to_json())
                    for p in enumerate_domain(old.schema))
                if v.status == "equivalent":
                    self.assertTrue(oracle_equal)
                else:
                    self.assertFalse(oracle_equal)
                    # Witness must be a real, replay-confirmed difference.
                    rec = point_to_record(v.witness)
                    self.assertNotEqual(
                        observable(interpret(old, rec).to_json()),
                        observable(interpret(new, rec).to_json()))

    def _load_verdict(self, name):
        doc = load(name)
        old, new = parse_case(doc)
        v = EquivalenceChecker(old, new, lambda s: build_solver(s)).run()
        return old, new, v


if __name__ == "__main__":
    unittest.main()
