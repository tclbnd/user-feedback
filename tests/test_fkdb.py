import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions import execute  # noqa: E402
from catalog import Database  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def run_example(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as handle:
        spec = json.load(handle)
    d = Database(spec)
    return [execute(d, stmt) for stmt in spec["statements"]], d.dump()


def db(on_delete):
    return Database({"tables": {"p": {"pk": "id"}, "c": {"pk": "id"}},
                     "rows": {"p": [{"id": 1}, {"id": 2}], "c": [{"id": 7, "p": 1}, {"id": 8, "p": 2}]},
                     "foreign_keys": [{"name": "fk", "table": "c", "column": "p", "ref_table": "p",
                                       "ref_column": "id", "on_delete": on_delete}]})


class FkTests(unittest.TestCase):
    def test_cascade_delete(self):
        d = db("cascade")
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r["deleted"], {"c": 1, "p": 1})
        self.assertEqual(sorted(d.rows["c"]), [8])

    def test_set_null(self):
        d = db("set_null")
        execute(d, {"op": "delete", "table": "p", "where": {"id": 2}})
        self.assertIsNone(d.rows["c"][8]["p"])

    def test_restrict_keeps_tables(self):
        d = db("restrict")
        r = execute(d, {"op": "delete", "table": "p", "where": {}})
        self.assertFalse(r["ok"])
        self.assertEqual(sorted(d.rows["p"]), [1, 2])


class ExampleTests(unittest.TestCase):
    """The shipped scripts, statement by statement."""

    def test_orders(self):
        results, tables = run_example("orders.json")
        self.assertEqual(results, [
            # Moving customer 2 to 3 hits note 901's on_update restrict.
            {"ok": False, "error": {"code": "foreign_key_violation",
                                    "constraint": "fk_note_customer",
                                    "row": ["note", 901], "at": ["customer", 2],
                                    "phase": "immediate"}},
            # Deleting customer 1 cascades to orders, invoices, lines; note 900
            # falls back to customer 0.
            {"ok": True, "deleted": {"customer": 1, "invoice": 1, "line": 2, "orders": 2},
             "updated": {"note": 1}},
            # customer 0 is the default note 900 now points at, but not a row
            # the walk-in note can be moved to once 0 itself goes away.
            {"ok": False, "error": {"code": "foreign_key_violation",
                                    "constraint": "fk_note_customer",
                                    "row": ["note", 900], "at": ["customer", 0],
                                    "phase": "statement_end"}},
        ])
        self.assertEqual(tables, {
            "customer": [{"id": 0, "name": "walk-in"}, {"id": 2, "name": "Globex"}],
            "orders": [{"id": 12, "customer_id": 2}],
            "line": [{"id": 102, "order_id": 12, "invoice_id": 501}],
            "invoice": [{"id": 501, "customer_id": 2}],
            "note": [{"id": 900, "customer_id": 0}, {"id": 901, "customer_id": 2}]})

    def test_tree(self):
        results, tables = run_example("tree.json")
        self.assertEqual(results, [
            {"ok": True, "deleted": {}, "updated": {"node": 3}},
            {"ok": True, "deleted": {}, "updated": {"node": 1}},
            {"ok": True, "deleted": {"node": 2}, "updated": {}},
            {"ok": True, "deleted": {"node": 2}, "updated": {}},
        ])
        self.assertEqual(tables, {"node": [{"id": 4, "parent_id": 10, "name": "c"},
                                           {"id": 10, "parent_id": None, "name": "root"}]})


class PatternTests(unittest.TestCase):
    """Acceptance patterns: multi-level, diamond, self-reference, cycles."""

    def test_multi_level_cascade_delete(self):
        d = Database({
            "tables": {"region": {"pk": "id"}, "store": {"pk": "id"},
                       "shelf": {"pk": "id"}, "item": {"pk": "id"}},
            "rows": {"region": [{"id": 1}, {"id": 2}],
                     "store": [{"id": 10, "region_id": 1}, {"id": 11, "region_id": 2}],
                     "shelf": [{"id": 100, "store_id": 10}, {"id": 101, "store_id": 11}],
                     "item": [{"id": 1000, "shelf_id": 100}, {"id": 1001, "shelf_id": 101},
                              {"id": 1002, "shelf_id": 101}]},
            "foreign_keys": [
                {"name": "fk_store_region", "table": "store", "column": "region_id",
                 "ref_table": "region", "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_shelf_store", "table": "shelf", "column": "store_id",
                 "ref_table": "store", "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_item_shelf", "table": "item", "column": "shelf_id",
                 "ref_table": "shelf", "ref_column": "id", "on_delete": "cascade"}]})
        r = execute(d, {"op": "delete", "table": "region", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True,
                             "deleted": {"item": 1, "region": 1, "shelf": 1, "store": 1},
                             "updated": {}})
        self.assertEqual(d.dump(), {
            "region": [{"id": 2}],
            "store": [{"id": 11, "region_id": 2}],
            "shelf": [{"id": 101, "store_id": 11}],
            "item": [{"id": 1001, "shelf_id": 101}, {"id": 1002, "shelf_id": 101}]})

    def test_diamond_delete_handles_shared_row_once(self):
        d = Database({
            "tables": {"p": {"pk": "id"}, "a": {"pk": "id"}, "b": {"pk": "id"},
                       "leaf": {"pk": "id"}, "tail": {"pk": "id"}},
            "rows": {"p": [{"id": 1}],
                     "a": [{"id": 10, "p_id": 1}],
                     "b": [{"id": 20, "p_id": 1}],
                     "leaf": [{"id": 100, "a_id": 10, "b_id": 20},
                              {"id": 101, "a_id": 10, "b_id": 20}],
                     "tail": [{"id": 1000, "leaf_id": 100}, {"id": 1001, "leaf_id": 101}]},
            "foreign_keys": [
                {"name": "fk_a_p", "table": "a", "column": "p_id", "ref_table": "p",
                 "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_b_p", "table": "b", "column": "p_id", "ref_table": "p",
                 "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_leaf_a", "table": "leaf", "column": "a_id", "ref_table": "a",
                 "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_leaf_b", "table": "leaf", "column": "b_id", "ref_table": "b",
                 "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_tail_leaf", "table": "tail", "column": "leaf_id",
                 "ref_table": "leaf", "ref_column": "id", "on_delete": "cascade"}]})
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True,
                             "deleted": {"a": 1, "b": 1, "leaf": 2, "p": 1, "tail": 2},
                             "updated": {}})
        self.assertEqual(d.dump(), {t: [] for t in d.tables})

    def test_diamond_update_touches_shared_row_once(self):
        d = Database({
            "tables": {"p": {"pk": "id"}, "leaf": {"pk": "id"}},
            "rows": {"p": [{"id": 1}], "leaf": [{"id": 100, "p1": 1, "p2": 1}]},
            "foreign_keys": [
                {"name": "fk_leaf_p1", "table": "leaf", "column": "p1", "ref_table": "p",
                 "ref_column": "id", "on_update": "cascade"},
                {"name": "fk_leaf_p2", "table": "leaf", "column": "p2", "ref_table": "p",
                 "ref_column": "id", "on_update": "cascade"}]})
        r = execute(d, {"op": "update", "table": "p", "where": {"id": 1}, "set": {"id": 2}})
        self.assertEqual(r, {"ok": True, "deleted": {}, "updated": {"leaf": 1, "p": 1}})
        self.assertEqual(d.dump(), {"p": [{"id": 2}], "leaf": [{"id": 100, "p1": 2, "p2": 2}]})

    def test_diamond_set_null_converges(self):
        d = Database({
            "tables": {"p": {"pk": "id"}, "leaf": {"pk": "id"}},
            "rows": {"p": [{"id": 1}], "leaf": [{"id": 100, "p1": 1, "p2": 1}]},
            "foreign_keys": [
                {"name": "fk_leaf_p1", "table": "leaf", "column": "p1", "ref_table": "p",
                 "ref_column": "id", "on_delete": "set_null"},
                {"name": "fk_leaf_p2", "table": "leaf", "column": "p2", "ref_table": "p",
                 "ref_column": "id", "on_delete": "set_null"}]})
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True, "deleted": {"p": 1}, "updated": {"leaf": 1}})
        self.assertEqual(d.dump(), {"p": [], "leaf": [{"id": 100, "p1": None, "p2": None}]})

    def _tree(self):
        return Database({
            "tables": {"node": {"pk": "id"}},
            "rows": {"node": [{"id": 1, "parent_id": None}, {"id": 2, "parent_id": 1},
                              {"id": 3, "parent_id": 2}, {"id": 4, "parent_id": 1},
                              {"id": 5, "parent_id": 6}, {"id": 6, "parent_id": 5}]},
            "foreign_keys": [{"name": "fk_node_parent", "table": "node", "column": "parent_id",
                              "ref_table": "node", "ref_column": "id",
                              "on_delete": "cascade", "on_update": "cascade"}]})

    def test_self_reference_update_subtree(self):
        d = self._tree()
        r = execute(d, {"op": "update", "table": "node", "where": {"id": 1}, "set": {"id": 10}})
        self.assertEqual(r, {"ok": True, "deleted": {}, "updated": {"node": 3}})
        self.assertEqual(d.dump()["node"], [
            {"id": 2, "parent_id": 10}, {"id": 3, "parent_id": 2},
            {"id": 4, "parent_id": 10}, {"id": 5, "parent_id": 6},
            {"id": 6, "parent_id": 5}, {"id": 10, "parent_id": None}])

    def test_self_reference_update_without_key_change(self):
        d = self._tree()
        r = execute(d, {"op": "update", "table": "node", "where": {"id": 5},
                        "set": {"parent_id": 6}})
        self.assertEqual(r, {"ok": True, "deleted": {}, "updated": {"node": 1}})
        self.assertNotIn(6, [row["parent_id"] for row in d.dump()["node"] if row["id"] == 6])

    def test_self_reference_delete_cycle_terminates(self):
        d = self._tree()
        r = execute(d, {"op": "delete", "table": "node", "where": {"id": 6}})
        self.assertEqual(r, {"ok": True, "deleted": {"node": 2}, "updated": {}})
        self.assertEqual([row["id"] for row in d.dump()["node"]], [1, 2, 3, 4])

    def test_self_reference_update_cycle_terminates(self):
        d = self._tree()
        r = execute(d, {"op": "update", "table": "node", "where": {"id": 5}, "set": {"id": 50}})
        self.assertEqual(r, {"ok": True, "deleted": {}, "updated": {"node": 2}})
        self.assertEqual(d.dump()["node"], [
            {"id": 1, "parent_id": None}, {"id": 2, "parent_id": 1},
            {"id": 3, "parent_id": 2}, {"id": 4, "parent_id": 1},
            {"id": 6, "parent_id": 50}, {"id": 50, "parent_id": 6}])

    def test_self_reference_delete_root(self):
        d = self._tree()
        r = execute(d, {"op": "delete", "table": "node", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True, "deleted": {"node": 4}, "updated": {}})
        self.assertEqual([row["id"] for row in d.dump()["node"]], [5, 6])

    def test_self_reference_delete_all_targets_counted_once(self):
        d = self._tree()
        r = execute(d, {"op": "delete", "table": "node", "where": {}})
        self.assertEqual(r, {"ok": True, "deleted": {"node": 6}, "updated": {}})
        self.assertEqual(d.dump()["node"], [])

    def test_circular_cascade_delete(self):
        d = Database({
            "tables": {"a": {"pk": "id"}, "b": {"pk": "id"}},
            "rows": {"a": [{"id": 1, "b_id": 10}], "b": [{"id": 10, "a_id": 1}]},
            "foreign_keys": [
                {"name": "fk_a_b", "table": "a", "column": "b_id", "ref_table": "b",
                 "ref_column": "id", "on_delete": "cascade", "on_update": "cascade"},
                {"name": "fk_b_a", "table": "b", "column": "a_id", "ref_table": "a",
                 "ref_column": "id", "on_delete": "cascade", "on_update": "cascade"}]})
        r = execute(d, {"op": "delete", "table": "a", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True, "deleted": {"a": 1, "b": 1}, "updated": {}})
        self.assertEqual(d.dump(), {"a": [], "b": []})

    def test_circular_cascade_three_tables(self):
        d = Database({
            "tables": {"x": {"pk": "id"}, "y": {"pk": "id"}, "z": {"pk": "id"}},
            "rows": {"x": [{"id": 1, "y_id": 10}], "y": [{"id": 10, "z_id": 20}],
                     "z": [{"id": 20, "x_id": 1}]},
            "foreign_keys": [
                {"name": "fk_x_y", "table": "x", "column": "y_id", "ref_table": "y",
                 "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_y_z", "table": "y", "column": "z_id", "ref_table": "z",
                 "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_z_x", "table": "z", "column": "x_id", "ref_table": "x",
                 "ref_column": "id", "on_delete": "cascade"}]})
        r = execute(d, {"op": "delete", "table": "x", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True, "deleted": {"x": 1, "y": 1, "z": 1}, "updated": {}})
        self.assertEqual(d.dump(), {"x": [], "y": [], "z": []})

    def test_circular_update_terminates(self):
        def d():
            return Database({
                "tables": {"a": {"pk": "id"}, "b": {"pk": "id"}},
                "rows": {"a": [{"id": 1, "b_id": 10}], "b": [{"id": 10, "a_id": 1}]},
                "foreign_keys": [
                    {"name": "fk_a_b", "table": "a", "column": "b_id", "ref_table": "b",
                     "ref_column": "id", "on_update": "cascade"},
                    {"name": "fk_b_a", "table": "b", "column": "a_id", "ref_table": "a",
                     "ref_column": "id", "on_update": "cascade"}]})
        db = d()
        r = execute(db, {"op": "update", "table": "a", "where": {"id": 1}, "set": {"id": 2}})
        self.assertEqual(r, {"ok": True, "deleted": {}, "updated": {"a": 1, "b": 1}})
        self.assertEqual(db.dump(), {"a": [{"id": 2, "b_id": 10}],
                                     "b": [{"id": 10, "a_id": 2}]})

    def test_cycle_where_primary_key_is_the_foreign_key(self):
        # a.id references b.id and b.id references a.id; both are PK and FK.
        def d():
            return Database({
                "tables": {"a": {"pk": "id"}, "b": {"pk": "id"}},
                "rows": {"a": [{"id": 1}], "b": [{"id": 1}]},
                "foreign_keys": [
                    {"name": "fk_a_b", "table": "a", "column": "id", "ref_table": "b",
                     "ref_column": "id", "on_update": "cascade", "on_delete": "cascade"},
                    {"name": "fk_b_a", "table": "b", "column": "id", "ref_table": "a",
                     "ref_column": "id", "on_update": "cascade", "on_delete": "cascade"}]})
        db = d()
        r = execute(db, {"op": "update", "table": "a", "where": {"id": 1}, "set": {"id": 2}})
        self.assertEqual(r, {"ok": True, "deleted": {}, "updated": {"a": 1, "b": 1}})
        self.assertEqual(db.dump(), {"a": [{"id": 2}], "b": [{"id": 2}]})
        r = execute(db, {"op": "delete", "table": "a", "where": {"id": 2}})
        self.assertEqual(r, {"ok": True, "deleted": {"a": 1, "b": 1}, "updated": {}})
        self.assertEqual(db.dump(), {"a": [], "b": []})

    def test_three_table_update_ring_terminates(self):
        d = Database({
            "tables": {"x": {"pk": "id"}, "y": {"pk": "id"}, "z": {"pk": "id"}},
            "rows": {"x": [{"id": 1, "y_id": 10}], "y": [{"id": 10, "z_id": 20}],
                     "z": [{"id": 20, "x_id": 1}]},
            "foreign_keys": [
                {"name": "fk_x_y", "table": "x", "column": "y_id", "ref_table": "y",
                 "ref_column": "id", "on_update": "cascade"},
                {"name": "fk_y_z", "table": "y", "column": "z_id", "ref_table": "z",
                 "ref_column": "id", "on_update": "cascade"},
                {"name": "fk_z_x", "table": "z", "column": "x_id", "ref_table": "x",
                 "ref_column": "id", "on_update": "cascade"}]})
        r = execute(d, {"op": "update", "table": "z", "where": {"id": 20}, "set": {"id": 30}})
        # z's key changes; y follows through z_id.  y's own key is unchanged,
        # so the ring stops there and x is not touched.
        self.assertEqual(r, {"ok": True, "deleted": {}, "updated": {"y": 1, "z": 1}})
        self.assertEqual(d.dump(), {"x": [{"id": 1, "y_id": 10}],
                                    "y": [{"id": 10, "z_id": 30}],
                                    "z": [{"id": 30, "x_id": 1}]})


class ActionTests(unittest.TestCase):
    """Each action, its phase and the position reported for a violation."""

    def _db(self, action, defaults=None, extra_parent=False):
        p = [{"id": 1, "name": "one"}]
        if extra_parent:
            p.insert(0, {"id": 0, "name": "zero"})
        return Database({
            "tables": {"p": {"pk": "id"}, "c": {"pk": "id", "defaults": defaults or {}}},
            "rows": {"p": p, "c": [{"id": 7, "p": 1}, {"id": 8, "p": 1 if extra_parent else 1}]},
            "foreign_keys": [{"name": "fk_c_p", "table": "c", "column": "p", "ref_table": "p",
                              "ref_column": "id", "on_delete": action, "on_update": action}]})

    def test_restrict_delete_is_immediate(self):
        d = self._db("restrict")
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": False, "error": {
            "code": "foreign_key_violation", "constraint": "fk_c_p",
            "row": ["c", 7], "at": ["p", 1], "phase": "immediate"}})
        self.assertEqual(d.dump()["p"], [{"id": 1, "name": "one"}])
        self.assertEqual(d.dump()["c"], [{"id": 7, "p": 1}, {"id": 8, "p": 1}])

    def test_no_action_delete_is_checked_at_statement_end(self):
        d = self._db("no_action")
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": False, "error": {
            "code": "foreign_key_violation", "constraint": "fk_c_p",
            "row": ["c", 7], "at": ["p", 1], "phase": "statement_end"}})
        self.assertEqual(sorted(d.rows["p"]), [1])

    def test_set_null(self):
        d = self._db("set_null")
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True, "deleted": {"p": 1}, "updated": {"c": 2}})
        self.assertEqual(d.dump()["c"], [{"id": 7, "p": None}, {"id": 8, "p": None}])

    def test_set_default_with_existing_parent(self):
        d = self._db("set_default", defaults={"p": 0}, extra_parent=True)
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True, "deleted": {"p": 1}, "updated": {"c": 2}})
        self.assertEqual(d.dump()["c"], [{"id": 7, "p": 0}, {"id": 8, "p": 0}])

    def test_set_default_without_parent_fails_at_statement_end(self):
        d = self._db("set_default", defaults={"p": 0})
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": False, "error": {
            "code": "foreign_key_violation", "constraint": "fk_c_p",
            "row": ["c", 7], "at": ["p", 1], "phase": "statement_end"}})
        self.assertEqual(sorted(d.rows["p"]), [1])
        self.assertEqual([row["p"] for row in d.rows["c"].values()], [1, 1])

    def test_cascade_update_only_when_referenced_column_changes(self):
        d = self._db("cascade")
        r = execute(d, {"op": "update", "table": "p", "where": {"id": 1},
                        "set": {"name": "renamed"}})
        self.assertEqual(r, {"ok": True, "deleted": {}, "updated": {"p": 1}})
        self.assertEqual([row["p"] for row in d.rows["c"].values()], [1, 1])

    def test_cascade_update_moves_children(self):
        d = self._db("cascade")
        r = execute(d, {"op": "update", "table": "p", "where": {"id": 1}, "set": {"id": 2}})
        self.assertEqual(r, {"ok": True, "deleted": {}, "updated": {"c": 2, "p": 1}})
        self.assertEqual(d.dump()["c"], [{"id": 7, "p": 2}, {"id": 8, "p": 2}])

    def test_restrict_update_is_immediate(self):
        d = self._db("restrict")
        r = execute(d, {"op": "update", "table": "p", "where": {"id": 1}, "set": {"id": 2}})
        self.assertEqual(r["error"]["phase"], "immediate")
        self.assertEqual(r["error"]["constraint"], "fk_c_p")
        self.assertEqual(r["error"]["row"], ["c", 7])
        self.assertEqual(r["error"]["at"], ["p", 1])
        self.assertEqual(sorted(d.rows["p"]), [1])

    def test_no_action_update_is_checked_at_statement_end(self):
        d = self._db("no_action")
        r = execute(d, {"op": "update", "table": "p", "where": {"id": 1}, "set": {"id": 2}})
        self.assertEqual(r["error"]["phase"], "statement_end")
        self.assertEqual(sorted(d.rows["p"]), [1])

    def test_deep_restrict_reports_the_row_that_hit_it(self):
        d = Database({
            "tables": {"p": {"pk": "id"}, "c": {"pk": "id"}, "g": {"pk": "id"}},
            "rows": {"p": [{"id": 1}], "c": [{"id": 10, "p_id": 1}],
                     "g": [{"id": 100, "c_id": 10}]},
            "foreign_keys": [
                {"name": "fk_c_p", "table": "c", "column": "p_id", "ref_table": "p",
                 "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_g_c", "table": "g", "column": "c_id", "ref_table": "c",
                 "ref_column": "id", "on_delete": "restrict"}]})
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": False, "error": {
            "code": "foreign_key_violation", "constraint": "fk_g_c",
            "row": ["g", 100], "at": ["c", 10], "phase": "immediate"}})
        self.assertEqual(sorted(d.rows["p"]), [1])
        self.assertEqual(sorted(d.rows["c"]), [10])

    def test_deep_no_action_reports_the_row_that_hit_it(self):
        d = Database({
            "tables": {"p": {"pk": "id"}, "c": {"pk": "id"}, "g": {"pk": "id"}},
            "rows": {"p": [{"id": 1}], "c": [{"id": 10, "p_id": 1}],
                     "g": [{"id": 100, "c_id": 10}]},
            "foreign_keys": [
                {"name": "fk_c_p", "table": "c", "column": "p_id", "ref_table": "p",
                 "ref_column": "id", "on_delete": "cascade"},
                {"name": "fk_g_c", "table": "g", "column": "c_id", "ref_table": "c",
                 "ref_column": "id", "on_delete": "no_action"}]})
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r["error"], {"code": "foreign_key_violation",
                                      "constraint": "fk_g_c", "row": ["g", 100],
                                      "at": ["c", 10], "phase": "statement_end"})
        self.assertEqual(sorted(d.rows["p"]), [1])

    def _two_fk_db(self, first_action, second_action):
        return Database({
            "tables": {"p": {"pk": "id"}, "c": {"pk": "id"}},
            "rows": {"p": [{"id": 1}], "c": [{"id": 7, "x": 1, "y": 1}]},
            "foreign_keys": [
                {"name": "fk_x", "table": "c", "column": "x", "ref_table": "p",
                 "ref_column": "id", "on_delete": first_action},
                {"name": "fk_y", "table": "c", "column": "y", "ref_table": "p",
                 "ref_column": "id", "on_delete": second_action}]})

    def test_no_action_passes_when_cascade_removed_the_row_first(self):
        d = self._two_fk_db("cascade", "no_action")
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True, "deleted": {"c": 1, "p": 1}, "updated": {}})
        self.assertEqual(d.dump(), {"p": [], "c": []})

    def test_restrict_sees_a_row_before_a_later_cascade_removes_it(self):
        d = self._two_fk_db("restrict", "cascade")
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r["error"]["constraint"], "fk_x")
        self.assertEqual(r["error"]["phase"], "immediate")
        self.assertEqual(sorted(d.rows["p"]), [1])

    def test_definition_order_lets_cascade_hide_the_row_from_restrict(self):
        d = self._two_fk_db("cascade", "restrict")
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True, "deleted": {"c": 1, "p": 1}, "updated": {}})
        self.assertEqual(d.dump(), {"p": [], "c": []})

    def test_row_deleted_later_is_not_counted_as_updated(self):
        d = self._two_fk_db("set_null", "cascade")
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r, {"ok": True, "deleted": {"c": 1, "p": 1}, "updated": {}})

    def test_restrict_uses_lowest_primary_key_row(self):
        d = Database({
            "tables": {"p": {"pk": "id"}, "c": {"pk": "id"}},
            "rows": {"p": [{"id": 1}], "c": [{"id": 9, "p": 1}, {"id": 3, "p": 1}]},
            "foreign_keys": [{"name": "fk_c_p", "table": "c", "column": "p", "ref_table": "p",
                              "ref_column": "id", "on_delete": "restrict"}]})
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r["error"]["row"], ["c", 3])

    def test_referencing_keys_are_applied_in_definition_order(self):
        # Both tables restrict: the constraint reported is the first one in
        # definition order, not the first table name alphabetically.
        d = Database({
            "tables": {"p": {"pk": "id"}, "a": {"pk": "id"}, "z": {"pk": "id"}},
            "rows": {"p": [{"id": 1}], "a": [{"id": 10, "p_id": 1}],
                     "z": [{"id": 20, "p_id": 1}]},
            "foreign_keys": [
                {"name": "fk_z_p", "table": "z", "column": "p_id", "ref_table": "p",
                 "ref_column": "id", "on_delete": "restrict"},
                {"name": "fk_a_p", "table": "a", "column": "p_id", "ref_table": "p",
                 "ref_column": "id", "on_delete": "restrict"}]})
        r = execute(d, {"op": "delete", "table": "p", "where": {"id": 1}})
        self.assertEqual(r["error"]["constraint"], "fk_z_p")
        self.assertEqual(r["error"]["row"], ["z", 20])
        self.assertEqual(sorted(d.rows["p"]), [1])

    def test_set_default_parent_deleted_in_same_statement(self):
        d = Database({
            "tables": {"p": {"pk": "id"}, "c": {"pk": "id", "defaults": {"p": 0}}},
            "rows": {"p": [{"id": 0}, {"id": 1}], "c": [{"id": 7, "p": 1}]},
            "foreign_keys": [{"name": "fk_c_p", "table": "c", "column": "p", "ref_table": "p",
                              "ref_column": "id", "on_delete": "set_default"}]})
        r = execute(d, {"op": "delete", "table": "p", "where": {}})
        self.assertEqual(r, {"ok": False, "error": {
            "code": "foreign_key_violation", "constraint": "fk_c_p",
            "row": ["c", 7], "at": ["p", 1], "phase": "statement_end"}})
        self.assertEqual(sorted(d.rows["p"]), [0, 1])
        self.assertEqual(d.rows["c"][7]["p"], 1)

    def test_set_default_child_deleted_later_passes(self):
        # p1's delete sets c.p1 to its default (which has no parent); the
        # delete of p2 then cascades that same row away, so the deferred
        # check is moot and the row counts as deleted, not updated.
        d = Database({
            "tables": {"p": {"pk": "id"}, "c": {"pk": "id", "defaults": {"p1": 0}}},
            "rows": {"p": [{"id": 1}, {"id": 2}], "c": [{"id": 7, "p1": 1, "p2": 2}]},
            "foreign_keys": [
                {"name": "fk_c_p1", "table": "c", "column": "p1", "ref_table": "p",
                 "ref_column": "id", "on_delete": "set_default"},
                {"name": "fk_c_p2", "table": "c", "column": "p2", "ref_table": "p",
                 "ref_column": "id", "on_delete": "cascade"}]})
        r = execute(d, {"op": "delete", "table": "p", "where": {}})
        self.assertEqual(r, {"ok": True, "deleted": {"c": 1, "p": 2}, "updated": {}})
        self.assertEqual(d.dump(), {"p": [], "c": []})


if __name__ == "__main__":
    unittest.main()
