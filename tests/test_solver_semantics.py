"""Unit tests for the finite semantics and the native solver.

Run with: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etleq import terms as T
from etleq.solver.native import NativeSolver, _NullFlag, SymIsNullB


def field_spec(bits=8, nullable=True, optional=True):
    class Spec:
        pass
    s = Spec()
    s.bits = bits
    s.signed = True
    s.nullable = nullable
    s.optional = optional
    s.domain_lo = lambda: -(1 << (bits - 1))
    s.domain_hi = lambda: (1 << (bits - 1)) - 1
    return s


class TestPresenceAndNull(unittest.TestCase):
    def setUp(self):
        self.schema = {"x": field_spec()}

    def test_missing_possible(self):
        r = NativeSolver(self.schema).check(T.not_b(T.Presence("x")))
        self.assertEqual(r.result, "sat")

    def test_null_requires_presence(self):
        q = T.and_b([_NullFlag("x"), T.not_b(T.Presence("x"))])
        self.assertEqual(NativeSolver(self.schema).check(q).result, "unsat")

    def test_null_distinct_from_value(self):
        iv = T.IVar(8, "x")
        q = T.and_b([_NullFlag("x"), T.not_b(T.IsNullI(iv))])
        self.assertEqual(NativeSolver(self.schema).check(q).result, "unsat")

    def test_value_input_is_not_null(self):
        iv = T.IVar(8, "x")
        q = T.and_b([T.Presence("x"), T.not_b(_NullFlag("x")), T.IsNullI(iv)])
        self.assertEqual(NativeSolver(self.schema).check(q).result, "unsat")


class TestFixedWidth(unittest.TestCase):
    def setUp(self):
        self.schema = {"x": field_spec()}

    def test_max_plus_one_wraps(self):
        iv = T.IVar(8, "x")
        w = T.wrap(T.bin_i("add", iv, T.IConst(8, 1), 8), 8)
        q = T.and_b([T.cmp_b("eq", iv, T.IConst(8, 127)),
                     T.cmp_b("eq", w, T.IConst(8, -128))])
        self.assertEqual(NativeSolver(self.schema).check(q).result, "sat")

    def test_identical_expressions_cannot_differ(self):
        iv = T.IVar(8, "x")
        w = T.wrap(T.bin_i("add", iv, T.IConst(8, 1), 8), 8)
        w2 = T.wrap(T.bin_i("add", iv, T.IConst(8, 1), 8), 8)
        q = T.not_b(T.cmp_b("eq", w, w2))
        self.assertEqual(NativeSolver(self.schema).check(q).result, "unsat")

    def test_value_out_of_range_unsat(self):
        iv = T.IVar(8, "x")
        q = T.cmp_b("gt", iv, T.IConst(8, 127))
        self.assertEqual(NativeSolver(self.schema).check(q).result, "unsat")


class TestDivisionGuards(unittest.TestCase):
    def setUp(self):
        self.schema = {"x": field_spec(nullable=False, optional=False)}

    def test_div_zero_guard(self):
        iv = T.IVar(8, "x")
        guard = T.and_b([T.not_b(T.IsNullI(iv)),
                         T.cmp_b("eq", iv, T.IConst(8, 0))])
        self.assertEqual(NativeSolver(self.schema).check(guard).result, "sat")

    def test_div_zero_guard_excludes_nonzero(self):
        iv = T.IVar(8, "x")
        guard = T.and_b([T.cmp_b("eq", iv, T.IConst(8, 1)),
                         T.cmp_b("eq", iv, T.IConst(8, 0))])
        self.assertEqual(NativeSolver(self.schema).check(guard).result, "unsat")


class TestThreeValuedBooleans(unittest.TestCase):
    def setUp(self):
        self.schema = {"x": field_spec()}

    def test_null_predicate_false_on_value(self):
        iv = T.IVar(8, "x")
        cmp_null = T.ite_b(T.OrB((T.IsNullI(iv),)), T.BNULL,
                           T.cmp_b("eq", iv, T.IConst(8, 5)))
        q = T.and_b([T.Presence("x"), T.not_b(T.IsNullI(iv)),
                     SymIsNullB(cmp_null)])
        self.assertEqual(NativeSolver(self.schema).check(q).result, "unsat")

    def test_null_predicate_true_on_null(self):
        iv = T.IVar(8, "x")
        cmp_null = T.ite_b(T.OrB((T.IsNullI(iv),)), T.BNULL,
                           T.cmp_b("eq", iv, T.IConst(8, 5)))
        q = T.and_b([T.Presence("x"), T.IsNullI(iv), SymIsNullB(cmp_null)])
        self.assertEqual(NativeSolver(self.schema).check(q).result, "sat")


class TestDiscreteIteSelection(unittest.TestCase):
    def setUp(self):
        self.schema = {"x": field_spec()}

    def test_nested_ite_selects_by_condition(self):
        ite = T.ite_i(
            T.not_b(T.Presence("x")), T.IConst(32, 7),
            T.ite_i(_NullFlag("x"), T.IConst(32, 8), T.IConst(32, 0)))
        # On a NULL input the missing branch (7) must not be selectable.
        q_bad = T.and_b([_NullFlag("x"),
                         T.cmp_b("eq", ite, T.IConst(32, 7))])
        self.assertEqual(NativeSolver(self.schema).check(q_bad).result, "unsat")
        q_good = T.and_b([_NullFlag("x"),
                          T.cmp_b("eq", ite, T.IConst(32, 8))])
        self.assertEqual(NativeSolver(self.schema).check(q_good).result, "sat")


if __name__ == "__main__":
    unittest.main()
