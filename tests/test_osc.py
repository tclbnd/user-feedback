import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from table import Database  # noqa: E402
from migrate import Failure, Migration  # noqa: E402


# --------------------------------------------------------------------------- #
# Independent reference model: replays a workload straight against a plain
# in-memory table using the same business semantics as table.Database.apply,
# but contains zero OSC logic. It is the oracle the migrated result is judged
# against row by row.
# --------------------------------------------------------------------------- #
class ReferenceTable:
    def __init__(self, pk, columns, defaults, rows):
        self.pk, self.columns, self.defaults = pk, list(columns), dict(defaults)
        self.rows = {r[pk]: dict(r) for r in rows}

    def _complete(self, values):
        return {c: (values[c] if c in values else self.defaults.get(c))
                for c in self.columns}

    def apply(self, w):
        if w["op"] == "insert":
            key = w["row"].get(self.pk)
            if key in self.rows:  # inserting an existing key is a no-op
                return
            self.rows[key] = self._complete(w["row"])
        elif w["op"] == "update":
            if w["pk"] not in self.rows:  # touching a missing row is a no-op
                return
            merged = {**self.rows[w["pk"]],
                      **{c: v for c, v in w["set"].items() if c in self.columns}}
            if merged[self.pk] != w["pk"] and merged[self.pk] in self.rows:
                return  # would collide with an existing key
            del self.rows[w["pk"]]
            self.rows[merged[self.pk]] = merged
        else:
            self.rows.pop(w["pk"], None)

    def snapshot(self):
        return {k: self._complete(v) for k, v in sorted(self.rows.items())}


def reference_database(seed, workload):
    """Replay the whole workload against a fresh copy of the original table."""
    src = seed["tables"]["users"]
    ref = ReferenceTable(src["pk"], src["columns"], src.get("defaults", {}), src["rows"])
    for w in sorted(workload, key=lambda x: (x["tick"],)):
        ref.apply(w)
    return ref


def expected_migrated_rows(seed, workload, alter):
    """What the post-cutover table must contain, schema and rows."""
    ref = reference_database(seed, workload)
    drop = set(alter.get("drop", []))
    columns = [c for c in ref.columns if c not in drop]
    defaults = dict(ref.defaults)
    for col in alter.get("add", []):
        columns.append(col["name"])
        defaults[col["name"]] = col.get("default")
    out = {}
    for key, row in ref.rows.items():
        out[key] = {c: (row[c] if c in row else defaults.get(c)) for c in columns}
    return columns, out


# A workload that continuously writes/updates/deletes across the whole window,
# including delete-then-reinsert of the same key and primary-key changes.
def busy_workload(ticks=12):
    ops = []
    # tick 1: deletes that collide with the rows copied first.
    ops += [
        {"tick": 1, "op": "delete", "table": "users", "pk": 1},
        {"tick": 1, "op": "delete", "table": "users", "pk": 2},
    ]
    # tick 2: an update and a brand-new insert.
    ops += [
        {"tick": 2, "op": "update", "table": "users", "pk": 5, "set": {"name": "eva"}},
        {"tick": 2, "op": "insert", "table": "users",
         "row": {"id": 13, "name": "max", "email": "max@x"}},
    ]
    # tick 3: delete then re-insert the SAME key -> must end as the new row.
    ops += [
        {"tick": 3, "op": "delete", "table": "users", "pk": 6},
        {"tick": 3, "op": "insert", "table": "users",
         "row": {"id": 6, "name": "fay2", "email": "fay@x"}},
        # primary-key update 3 -> 30.
        {"tick": 3, "op": "update", "table": "users", "pk": 3, "set": {"id": 30}},
    ]
    # middle ticks: steady churn so replay spans several batches.
    next_id = 100
    for t in range(4, ticks - 1):
        ops.append({"tick": t, "op": "update", "table": "users",
                    "pk": 7, "set": {"name": "g" + str(t)}})
        ops.append({"tick": t, "op": "insert", "table": "users",
                    "row": {"id": next_id, "name": "n" + str(next_id), "email": "e"}})
        next_id += 1
        ops.append({"tick": t, "op": "delete", "table": "users", "pk": 4})
        ops.append({"tick": t, "op": "insert", "table": "users",
                    "row": {"id": 4, "name": "dan" + str(t), "email": "d"}})
    # late primary-key move near cut-over.
    ops.append({"tick": ticks - 1, "op": "update", "table": "users",
                "pk": 8, "set": {"id": 80}})
    ops.append({"tick": ticks, "op": "delete", "table": "users", "pk": 11})
    return ops


ALTER = {"table": "users", "add": [{"name": "tier", "default": "free"}],
         "drop": ["email"]}


class OscTests(unittest.TestCase):
    def test_trigger_and_lock(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "db.json")
            with open(path, "w") as fh:
                json.dump({"tables": {"t": {"pk": "id", "columns": ["id", "v"], "rows": [{"id": 1, "v": "a"}]},
                                      "log": {"pk": "seq", "columns": ["seq", "op", "pk", "old_pk", "row"], "rows": []}},
                           "triggers": [{"name": "trg", "table": "t", "log": "log"}]}, fh)
            db = Database(path, [{"tick": 1, "op": "insert", "table": "t", "row": {"id": 2, "v": "b"}}])
            db.lock("t")
            db.tick()
            self.assertNotIn(2, db.tables["t"]["rows"])
            db.unlock("t")
            self.assertEqual([r["op"] for r in db.tables["log"]["rows"].values()], ["insert"])

    def test_migrate_quiet_table(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.json")
            shutil.copy(os.path.join(ROOT, "examples", "users.json"), db)
            proc = subprocess.run([sys.executable, os.path.join(ROOT, "osc.py"), "migrate", db,
                                   os.path.join(ROOT, "examples", "alter.json")], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(db) as fh:
                data = json.load(fh)
            self.assertEqual(sorted(data["tables"]), ["users"])
            self.assertEqual(data["tables"]["users"]["columns"], ["id", "name", "tier"])
            self.assertEqual(len(data["tables"]["users"]["rows"]), 12)

    # --------------------------------------------------------------- helpers
    def _seed(self, d, name="db.json"):
        path = os.path.join(d, name)
        shutil.copy(os.path.join(ROOT, "examples", "users.json"), path)
        with open(os.path.join(ROOT, "examples", "users.json")) as fh:
            seed = json.load(fh)
        return path, seed

    @staticmethod
    def _rows_by_key(data, table="users"):
        t = data["tables"][table]
        return {r[t["pk"]]: r for r in t["rows"]}

    def _no_osc_residue(self, data):
        names = list(data["tables"])
        self.assertEqual([n for n in names if n.startswith("_osc_")], [], names)
        self.assertEqual(data.get("triggers", []), [])

    # ------------------------------------------------- 1. row-by-row equality
    def test_migrated_rows_match_reference_row_by_row(self):
        workload = busy_workload()
        with tempfile.TemporaryDirectory() as d:
            path, seed = self._seed(d)
            alter_path = os.path.join(d, "alter.json")
            with open(alter_path, "w") as fh:
                json.dump(ALTER, fh)
            wl_path = os.path.join(d, "workload.json")
            with open(wl_path, "w") as fh:
                json.dump(workload, fh)
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "osc.py"), "migrate", path,
                 alter_path, "--workload", wl_path, "--chunk", "3", "--max-lock", "2"],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)
            self.assertEqual(out["status"], "done")

            with open(path) as fh:
                data = json.load(fh)
            self._no_osc_residue(data)

            exp_cols, exp_rows = expected_migrated_rows(seed, workload, ALTER)
            got = data["tables"]["users"]
            self.assertEqual(got["columns"], exp_cols)
            got_rows = self._rows_by_key(data)
            self.assertEqual(sorted(got_rows), sorted(exp_rows))
            for key, exp in exp_rows.items():
                self.assertEqual(got_rows[key], exp, "row mismatch at key %r" % key)

    def test_special_cases_delete_reinsert_and_pk_move(self):
        # Focused semantic check: same-key delete/reinsert and a pk move, with
        # copy chunks interleaved between the conflicting operations.
        workload = [
            {"tick": 1, "op": "delete", "table": "users", "pk": 6},
            {"tick": 2, "op": "insert", "table": "users",
             "row": {"id": 6, "name": "fay2", "email": "fay@x"}},
            {"tick": 3, "op": "update", "table": "users", "pk": 3, "set": {"id": 30}},
            {"tick": 4, "op": "update", "table": "users", "pk": 30, "set": {"name": "cat2"}},
        ]
        with tempfile.TemporaryDirectory() as d:
            path, seed = self._seed(d)
            db = Database(path, workload)
            m = Migration(db, ALTER, chunk=2)
            m.run()
            db.finish()
            rows = db.tables["users"]["rows"]
            self.assertNotIn(3, rows)              # old key gone
            self.assertEqual(rows[30]["name"], "cat2")  # moved then updated
            self.assertEqual(rows[6]["name"], "fay2")   # re-inserted value wins
            self.assertNotIn(6, [r for r in rows if rows[r]["name"] == "fay"])
            self._no_osc_residue({"tables": db.tables, "triggers": db.triggers})

    # ------------------------------------------------------ 2. lock duration
    def test_lock_held_only_for_final_batch_and_under_threshold(self):
        workload = busy_workload()
        with tempfile.TemporaryDirectory() as d:
            path, _ = self._seed(d)
            db = Database(path, workload)
            m = Migration(db, ALTER, chunk=3)
            m.run()
            # The lock may only span the single final flush plus swap: no tick
            # (and therefore no chunk/batch of work) happens while locked.
            self.assertLessEqual(db.lock_ticks, 1)
            self.assertIn("replayed", m.summary)

    def test_cli_exit_4_when_lock_exceeds_max(self):
        workload = busy_workload()
        with tempfile.TemporaryDirectory() as d:
            path, _ = self._seed(d)
            alter_path = os.path.join(d, "alter.json")
            wl_path = os.path.join(d, "workload.json")
            with open(alter_path, "w") as fh:
                json.dump(ALTER, fh)
            with open(wl_path, "w") as fh:
                json.dump(workload, fh)
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "osc.py"), "migrate", path,
                 alter_path, "--workload", wl_path, "--chunk", "3", "--max-lock", "0"],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 4, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["status"], "done")

    # ------------------------------------------- 3. failure in every phase
    def test_rollback_after_each_injected_phase_leaves_table_untouched(self):
        for phase in ("copy", "replay", "cutover", "swap"):
            with self.subTest(phase=phase):
                workload = busy_workload()
                with tempfile.TemporaryDirectory() as d:
                    path, seed = self._seed(d, name="db_%s.json" % phase)

                    # Real run: inject a failure in this phase, then rollback.
                    db = Database(path, workload)
                    m = Migration(db, ALTER, chunk=3, fail_at=phase)
                    try:
                        m.run()
                        self.fail("failure was not injected at phase %s" % phase)
                    except Failure:
                        consumed_clock = db.clock
                        m.rollback()

                    # Independent oracle: replay ONLY the operations the failed
                    # run had actually consumed (tick <= clock at failure),
                    # straight against a plain table with no OSC machinery.
                    src = seed["tables"]["users"]
                    ref = ReferenceTable(src["pk"], src["columns"],
                                         src.get("defaults", {}), src["rows"])
                    for w in workload:
                        if w["tick"] <= consumed_clock:
                            ref.apply(w)
                    expected_rows = ref.snapshot()

                    actual = {n: {**t, "rows": [t["rows"][k] for k in sorted(t["rows"])]}
                              for n, t in db.tables.items()}
                    self.assertEqual(sorted(actual), ["users"])
                    self._no_osc_residue({"tables": db.tables, "triggers": db.triggers})
                    # Schema unchanged.
                    self.assertEqual(actual["users"]["columns"],
                                     seed["tables"]["users"]["columns"])
                    # Rows row-by-row equal to the independent oracle.
                    self.assertEqual(self._rows_by_key(
                        {"tables": actual}), expected_rows)
                    # No capture trigger may survive a rollback.
                    self.assertEqual(db.triggers, [])

    def test_cli_reports_rolled_back_and_saves_clean_state(self):
        workload = busy_workload()
        with tempfile.TemporaryDirectory() as d:
            path, seed = self._seed(d)
            alter_path = os.path.join(d, "alter.json")
            wl_path = os.path.join(d, "workload.json")
            with open(alter_path, "w") as fh:
                json.dump(ALTER, fh)
            with open(wl_path, "w") as fh:
                json.dump(workload, fh)
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "osc.py"), "migrate", path,
                 alter_path, "--workload", wl_path, "--chunk", "3", "--fail-at", "copy"],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 3, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "rolled_back")
            with open(path) as fh:
                data = json.load(fh)
            self._no_osc_residue(data)
            self.assertEqual(data["tables"]["users"]["columns"],
                             seed["tables"]["users"]["columns"])

    def test_rollback_is_idempotent_and_safe_without_setup(self):
        with tempfile.TemporaryDirectory() as d:
            path, _ = self._seed(d)
            db = Database(path, [])
            m = Migration(db, ALTER)
            m.rollback()          # nothing created yet -> must not raise
            m.rollback()
            self.assertEqual(sorted(db.tables), ["users"])

    # --------------------------------------- 4. queued writes after cut-over
    def test_queued_writes_land_on_new_table_and_match_oracle(self):
        # Operations scheduled while the lock is held must apply, at unlock, to
        # the table now carrying the original name (the migrated table).
        workload = [{"tick": 1, "op": "update", "table": "users", "pk": 1,
                     "set": {"name": "ann-locked"}}]
        with tempfile.TemporaryDirectory() as d:
            path, seed = self._seed(d)

            oracle = Database(path, workload)
            oracle.finish()
            expected_name = oracle.tables["users"]["rows"][1]["name"]

            db = Database(path, workload)
            m = Migration(db, ALTER, chunk=4)
            m.run()
            db.finish()
            self.assertEqual(db.tables["users"]["rows"][1]["name"], expected_name)
            # New schema applied to the row that was updated under the lock.
            self.assertIn("tier", db.tables["users"]["rows"][1])


if __name__ == "__main__":
    unittest.main()
