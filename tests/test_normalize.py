import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from design import analyze, bcnf, preserved, synthesize, violation, violations  # noqa: E402
from fd import candidate_keys, closure, merged, minimal_cover  # noqa: E402
from verify_join import load_rows, load_spec, natural_join_sql, build_database  # noqa: E402


def fd(spec_pairs):
    return [(list(l), list(r)) for l, r in spec_pairs]


class ClosureTests(unittest.TestCase):
    def test_closure(self):
        self.assertEqual(closure(["A"], [(["A"], ["B"]), (["B"], ["C"])]),
                         {"A", "B", "C"})

    def test_closure_needs_fixpoint_iteration(self):
        # AB -> D only becomes usable after A -> B fires.
        fds = [(["A"], ["B"]), (["B", "D"], ["E"]), (["A", "B"], ["D"])]
        self.assertEqual(closure(["A"], fds), {"A", "B", "D", "E"})

    def test_closure_with_empty_lhs(self):
        fds = [([], ["A"]), (["A"], ["B"])]
        self.assertEqual(closure([], fds), {"A", "B"})

    def test_closure_ordered_output_via_cli_contract(self):
        order = ["C", "B", "A"]
        plus = closure(["A"], [(["A"], ["B"]), (["B"], ["C"])])
        self.assertEqual([x for x in order if x in plus], ["C", "B", "A"])


class MinimalCoverTests(unittest.TestCase):
    def test_cover_splits_right_sides(self):
        cover = minimal_cover([(["A"], ["B", "C"])], ["A", "B", "C"])
        self.assertEqual(cover, [(("A",), ("B",)), (("A",), ("C",))])

    def test_trivial_and_duplicate_fds_dropped(self):
        cover = minimal_cover([(["A", "B"], ["A", "C"]),
                               (["A"], ["C"])], ["A", "B", "C"])
        self.assertEqual(cover, [(("A",), ("C",))])

    def test_extraneous_left_attribute_removed(self):
        # AB -> C is implied by A -> B, A -> C: B is extraneous on the left.
        fds = [(["A"], ["B"]), (["A"], ["C"]), (["A", "B"], ["C"])]
        cover = minimal_cover(fds, ["A", "B", "C"])
        self.assertNotIn((("A", "B"), ("C",)), cover)
        self.assertIn((("A",), ("C",)), cover)

    def test_redundant_dependency_dropped_in_order(self):
        # A -> C follows from A -> B, B -> C.
        cover = minimal_cover([(["A"], ["B"]), (["B"], ["C"]),
                               (["A"], ["C"])], ["A", "B", "C"])
        self.assertEqual(cover,
                         [(("A",), ("B",)), (("B",), ("C",))])

    def test_cover_is_equivalent_to_input(self):
        fds = fd([(("order_id",), ("order_date", "customer_id")),
                  (("customer_id",), ("customer_name", "city")),
                  (("street", "city"), ("zip",)),
                  (("zip",), ("city",))])
        order = ["order_id", "order_date", "customer_id", "customer_name",
                 "street", "city", "zip"]
        cover = minimal_cover(fds, order)
        for lhs, rhs in fds:
            for a in rhs:
                self.assertIn(a, closure(lhs, cover))
        for lhs, (a,) in cover:
            self.assertIn(a, closure(lhs, fds))

    def test_merged_groups_rhs_preserving_order(self):
        cover = [(("A",), ("B",)), (("A",), ("C",)), (("D",), ("E",))]
        self.assertEqual(merged(cover),
                         [(("A",), ["B", "C"]), (("D",), ["E",])])


class CandidateKeyTests(unittest.TestCase):
    def test_single_key_from_forced_attributes(self):
        fds = [(["A"], ["B"]), (["B"], ["C"])]
        self.assertEqual(candidate_keys(["A", "B", "C"], fds), [["A"]])

    def test_all_keys_of_different_sizes_are_returned(self):
        # C -> AB gives key {C}; AB -> C gives key {A,B}: sizes 1 and 2.
        fds = [(["A", "B"], ["C"]), (["C"], ["A", "B"])]
        keys = candidate_keys(["A", "B", "C", "D"], fds)
        self.assertIn(["C", "D"], keys)
        self.assertIn(["A", "B", "D"], keys)
        self.assertEqual(len(keys), 2)
        # output ordering: smallest sets first, then schema-order tiebreak
        self.assertEqual(keys, [["C", "D"], ["A", "B", "D"]])

    def test_two_composite_keys(self):
        fds = [(["emp_id"], ["emp_email", "dept"]),
               (["emp_email"], ["emp_id", "dept"]),
               (["emp_id", "project"], ["role", "hours"])]
        order = ["emp_id", "emp_email", "dept", "project", "role", "hours"]
        keys = candidate_keys(order, fds)
        self.assertEqual(keys,
                         [["emp_id", "project"], ["emp_email", "project"]])

    def test_no_dependencies_whole_tuple_is_key(self):
        self.assertEqual(candidate_keys(["A", "B"], []), [["A", "B"]])


class SynthesisTests(unittest.TestCase):
    def test_relation_contained_in_another_is_dropped(self):
        order = ["A", "B", "C"]
        cover = [(("A",), ("B",)), (("A",), ("C",))]
        rels = synthesize(order, cover, [["A"]])
        self.assertEqual(rels, [(["A", "B", "C"], ["A"])])

    def test_candidate_key_relation_added_when_missing(self):
        # FD A -> B leaves C only in the key; no cover relation holds the key.
        order = ["A", "B", "C"]
        cover = [(("A",), ("B",))]
        rels = synthesize(order, cover, [["A", "C"]])
        attrs = [a for a, _ in rels]
        self.assertIn(["A", "B"], attrs)
        self.assertIn(["A", "C"], attrs)

    def test_orders_example_synthesis(self):
        order = ["order_id", "line_no", "order_date", "customer_id",
                 "customer_name", "street", "city", "zip",
                 "product_id", "product_name", "unit_price", "qty"]
        fds = fd([(("order_id",), ("order_date", "customer_id")),
                  (("customer_id",), ("customer_name", "street", "city")),
                  (("street", "city"), ("zip",)),
                  (("zip",), ("city",)),
                  (("product_id",), ("product_name", "unit_price")),
                  (("order_id", "line_no"),
                   ("product_id", "qty", "customer_name"))])
        cover = minimal_cover(fds, order)
        keys = candidate_keys(order, cover)
        rels = synthesize(order, cover, keys)
        # every attribute appears in some relation and the key relation exists
        covered = {a for attrs, _ in rels for a in attrs}
        self.assertEqual(covered, set(order))
        self.assertTrue(any(set(keys[0]) <= set(attrs) for attrs, _ in rels))


class BCNFTests(unittest.TestCase):
    def test_zip_city_relation_violates_bcnf(self):
        fds = [(["street", "city"], ["zip"]), (["zip"], ["city"])]
        v = violation(["street", "city", "zip"], fds)
        self.assertEqual(v[0], ["zip"])  # smallest violating determinant

    def test_bcnf_split_loses_street_city_dependency(self):
        fds = [(("street", "city"), ("zip",)), (("zip",), ("city",))]
        rels = [(["street", "city", "zip"], ["street", "city"])]
        split = bcnf(rels, fds, ["street", "city", "zip"])
        self.assertEqual(sorted(map(tuple, split)),
                         [("city", "zip"), ("street", "zip")])
        self.assertFalse(preserved((("street", "city"), ("zip",)),
                                   split, fds))
        self.assertTrue(preserved((("zip",), ("city",)), split, fds))

    def test_preserved_dependency_spanning_relations_via_chase(self):
        # A -> B kept in R1, B -> C kept in R2: A -> C is still derivable by
        # the projected-closure test even though no single relation holds all.
        fds = [(["A"], ["B"]), (["B"], ["C"])]
        rels = [["A", "B"], ["B", "C"]]
        self.assertTrue(preserved((["A"], ["C"]), rels, fds))

    def test_enrollment_is_bcnf_without_loss(self):
        order = ["student", "course", "instructor", "room", "grade"]
        fds = fd([(("student", "course"), ("instructor", "grade")),
                  (("course",), ("instructor", "room"))])
        report = analyze({"table": "enrollment_wide",
                          "attributes": order, "fds": [
                              {"lhs": list(l), "rhs": list(r)}
                              for l, r in fds]},
                         minimal_cover(fds, order))
        self.assertTrue(report["is_bcnf"])
        self.assertEqual(report["lost_dependencies"], [])


class ViolationCheckTests(unittest.TestCase):
    def test_sample_violation(self):
        spec = {"fds": [{"lhs": ["zip"], "rhs": ["city"]}]}
        rows = [{"zip": "1", "city": "X"}, {"zip": "1", "city": "Y"}]
        self.assertEqual(violations(spec, rows)[0]["values"], ["1"])

    def test_multi_attribute_rhs_checked_fully(self):
        spec = {"fds": [{"lhs": ["id"], "rhs": ["name", "city"]}]}
        rows = [{"id": "1", "name": "Ann", "city": "X"},
                {"id": "1", "name": "Ann", "city": "Y"}]
        bad = violations(spec, rows)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["rhs"], ["name", "city"])

    def test_compliant_sample_has_no_violation(self):
        spec = {"fds": [{"lhs": ["id"], "rhs": ["name", "city"]}]}
        rows = [{"id": "1", "name": "Ann", "city": "X"},
                {"id": "2", "name": "Bo", "city": "Y"}]
        self.assertEqual(violations(spec, rows), [])

    def test_composite_left_side_groups(self):
        spec = {"fds": [{"lhs": ["a", "b"], "rhs": ["c"]}]}
        rows = [{"a": "1", "b": "1", "c": "x"},
                {"a": "1", "b": "2", "c": "y"},
                {"a": "1", "b": "1", "c": "z"}]
        self.assertEqual(violations(spec, rows)[0]["values"], ["1", "1"])


EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "examples")


class MigrationRoundTripTests(unittest.TestCase):
    def _round_trip(self, spec_name, csv_name, expect_bcnf, expect_lost):
        spec, fds = load_spec(os.path.join(EXAMPLES, spec_name))
        rows = load_rows(os.path.join(EXAMPLES, csv_name))
        self.assertEqual(violations(spec, rows), [])
        report = analyze(spec, minimal_cover(fds, spec["attributes"]))
        self.assertEqual(report["is_bcnf"], expect_bcnf)
        self.assertEqual(report["lost_dependencies"], expect_lost)

        con = build_database(spec, rows, report)
        joined = con.execute(natural_join_sql(spec, report)).fetchall()
        joined = [tuple("" if v is None else str(v) for v in t) for t in joined]
        original = [tuple(r[a] for a in spec["attributes"]) for r in rows]
        self.assertEqual(sorted(joined), sorted(original))
        # DISTINCT migration never duplicates rows vs. the wide table
        for rel in report["relations"]:
            n = con.execute(
                f"SELECT COUNT(*) FROM {rel['name']};").fetchone()[0]
            self.assertLessEqual(n, len(rows))

    def test_orders_round_trip_with_lost_bcnf_dependency(self):
        self._round_trip(
            "orders_wide.json", "sample.csv", False,
            [{"lhs": ["street", "city"], "rhs": ["zip"]}])

    def test_enrollment_round_trip_bcnf(self):
        self._round_trip("enrollment.json", "enrollment.csv", True, [])

    def test_emp_project_round_trip_bcnf(self):
        self._round_trip("emp_project.json", "emp_project.csv", True, [])

    def test_bad_samples_are_rejected(self):
        for spec_name, csv_name, lhs in [
                ("orders_wide.json", "sample_bad.csv", ["zip"]),
                ("emp_project.json", "emp_project_bad.csv", ["emp_id"])]:
            spec, _ = load_spec(os.path.join(EXAMPLES, spec_name))
            rows = load_rows(os.path.join(EXAMPLES, csv_name))
            bad = violations(spec, rows)
            self.assertTrue(bad)
            self.assertIn(lhs, [v["lhs"] for v in bad])


if __name__ == "__main__":
    unittest.main()

