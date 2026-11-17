"""Crash/reconnect tests: repeated syncs must converge to the publisher.

An independent reference model (written straight from the README rules, not
reusing apply.py) computes the expected replica, position and conflict log.
Syncs are then repeatedly killed inside transactions, before/after commits,
and before/after every file write, and the final state is compared row by row.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_child.py")


# --------------------------------------------------------------------------
# Reference model: the README apply rules, implemented from scratch.
# --------------------------------------------------------------------------
class Model:
    def __init__(self, seed=None):
        self.tables = {}
        self.position = 0
        self.conflicts = []
        if seed:
            for name, t in seed["tables"].items():
                self.tables[name] = {
                    "primary_key": t["primary_key"],
                    "columns": dict(t["columns"]),
                    "rows": [dict(r) for r in t["rows"]],
                }

    def row_for(self, table, key):
        for row in table["rows"]:
            if row.get(table["primary_key"]) == key:
                return row
        return None

    def apply(self, messages):
        i = 0
        while i < len(messages):
            m = messages[i]
            if m["type"] != "begin":
                raise AssertionError(f"expected begin at {m}")
            txn = []
            i += 1
            while messages[i]["type"] != "commit":
                txn.append(messages[i])
                i += 1
            commit_lsn = messages[i]["lsn"]
            i += 1
            for change in txn:
                self._change(change, commit_lsn)
            self.position = commit_lsn

    def _change(self, m, commit_lsn):
        if m["type"] == "ddl":
            self._ddl(m)
            return
        table = self.tables[m["table"]]
        pk = table["primary_key"]
        if m["type"] == "delete":
            row = self.row_for(table, m["key"])
            if row is None:
                self._conflict(m, commit_lsn, "delete_missing")
            else:
                table["rows"].remove(row)
            return
        if m["type"] == "update":
            row = self.row_for(table, m["key"])
            if row is None:
                self._conflict(m, commit_lsn, "update_missing")
                return
            table["rows"].remove(row)
            new = {c: m["row"].get(c) for c in table["columns"]}
            table["rows"].append(new)
            return
        new = {c: m["row"].get(c) for c in table["columns"]}
        existing = self.row_for(table, new[pk])
        if existing is not None:
            self._conflict(m, commit_lsn, "insert_exists", key=new[pk])
            table["rows"].remove(existing)
        table["rows"].append(new)

    def _ddl(self, m):
        if m["op"] == "create_table":
            self.tables[m["table"]] = {
                "primary_key": m["primary_key"],
                "columns": dict(m["columns"]),
                "rows": [],
            }
            return
        table = self.tables[m["table"]]
        if m["op"] == "add_column":
            table["columns"][m["column"]] = m.get("default")
            for row in table["rows"]:
                row[m["column"]] = m.get("default")
        elif m["op"] == "drop_column":
            table["columns"].pop(m["column"], None)
            for row in table["rows"]:
                row.pop(m["column"], None)

    def _conflict(self, m, commit_lsn, kind, key=None):
        self.conflicts.append({
            "commit_lsn": commit_lsn,
            "lsn": m["lsn"],
            "type": kind,
            "table": m["table"],
            "key": m["key"] if key is None else key,
        })

    def rendered_rows(self, name):
        table = self.tables[name]
        pk = table["primary_key"]
        return sorted(table["rows"], key=lambda r: (r.get(pk) is None, r.get(pk)))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def sorted_rows(rows, pk="id"):
    def key(r):
        v = r.get(pk)
        return (v is None, isinstance(v, str), v if not isinstance(v, str) else v)
    return sorted(rows, key=key)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_conflicts(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def run_child(replica, publisher, drop_after=None, kill_after=None, spec=None):
    env = dict(os.environ)
    env["CRASH_SPEC"] = json.dumps(spec or {})
    cmd = [sys.executable, CHILD, replica, publisher, str(drop_after), str(kill_after)]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    payload = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            payload = json.loads(line)
    return proc.returncode, payload


class FaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pub = os.path.join(self.tmp, "pub")
        os.makedirs(self.pub)
        with open(os.path.join(WORK, "examples", "changes.jsonl"), encoding="utf-8") as fh:
            self.log = [json.loads(line) for line in fh if line.strip()]
        with open(os.path.join(self.pub, "changes.jsonl"), "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(m) + "\n" for m in self.log)
        self.seed = read_json(os.path.join(WORK, "examples", "replica-seed.json"))

        self.model = Model(self.seed)
        self.model.apply(self.log)

    def new_replica(self, seed=True):
        rep = tempfile.mkdtemp(dir=self.tmp)
        if seed:
            shutil.copy(os.path.join(WORK, "examples", "replica-seed.json"),
                        os.path.join(rep, "replica.json"))
        return rep

    def assert_converged(self, rep):
        state = read_json(os.path.join(rep, "replica.json"))["tables"]
        self.assertEqual(sorted(state), sorted(self.model.tables))
        for name, expected in self.model.tables.items():
            actual = state[name]
            self.assertEqual(actual["primary_key"], expected["primary_key"], name)
            self.assertEqual(actual["columns"], expected["columns"], name)
            self.assertEqual(sorted_rows(actual["rows"], expected["primary_key"]),
                             sorted_rows(self.model.rendered_rows(name), expected["primary_key"]),
                             f"row-by-row mismatch in {name}")
        self.assertEqual(read_json(os.path.join(rep, "position.json")),
                         {"commit_lsn": self.model.position})
        self.assertEqual(read_conflicts(os.path.join(rep, "conflicts.jsonl")),
                         self.model.conflicts)

    # -- scenarios -------------------------------------------------------
    def test_clean_sync_matches_model_and_logs_conflicts(self):
        rep = self.new_replica()
        code, payload = run_child(rep, self.pub)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["result"]["applied"], 8)
        self.assert_converged(rep)
        self.assertEqual(len(self.model.conflicts), 2)  # insert_exists + delete_missing

    def test_every_drop_point_then_resume(self):
        total_messages = len(self.log)
        for drop in range(total_messages + 1):
            with self.subTest(drop=drop):
                rep = self.new_replica()
                slot = os.path.join(self.pub, "slot.json")
                if os.path.exists(slot):
                    os.remove(slot)
                code, _ = run_child(rep, self.pub, drop_after=drop)
                self.assertIn(code, (0, 75))
                code, _ = run_child(rep, self.pub)
                self.assertEqual(code, 0)
                self.assert_converged(rep)

    def test_every_receive_boundary_crash_then_resume(self):
        for index in range(1, len(self.log) + 1):
            for point in ("pre", "post"):
                with self.subTest(receive_index=index, point=point):
                    rep = self.new_replica()
                    slot = os.path.join(self.pub, "slot.json")
                    if os.path.exists(slot):
                        os.remove(slot)
                    code, _ = run_child(rep, self.pub,
                                        spec={"receive_index": index, "point": point})
                    self.assertEqual(code, 9)
                    code, _ = run_child(rep, self.pub)
                    self.assertEqual(code, 0)
                    self.assert_converged(rep)

    def test_reconnect_storm_converges(self):
        """Crash at a different, deliberately random point on every attempt."""
        import random
        rng = random.Random(1234)
        rep = self.new_replica()
        slot = os.path.join(self.pub, "slot.json")
        if os.path.exists(slot):
            os.remove(slot)
        for _ in range(40):
            spec = {"receive_index": rng.randint(1, len(self.log)),
                    "point": rng.choice(["pre", "post"])}
            code, _ = run_child(rep, self.pub, spec=spec)
            self.assertIn(code, (0, 9))
        code, _ = run_child(rep, self.pub)
        self.assertEqual(code, 0)
        self.assert_converged(rep)

    def test_every_file_write_boundary_crash_then_resume(self):
        code, payload = run_child(self.new_replica(), self.pub)
        self.assertEqual(code, 0)
        total_writes = payload["writes"]
        self.assertGreater(total_writes, 0)
        for index in range(1, total_writes + 1):
            for point in ("pre", "post"):
                with self.subTest(write_index=index, point=point):
                    rep = self.new_replica()
                    slot = os.path.join(self.pub, "slot.json")
                    if os.path.exists(slot):
                        os.remove(slot)
                    code, _ = run_child(rep, self.pub,
                                        spec={"write_index": index, "point": point})
                    self.assertEqual(code, 9)
                    code, _ = run_child(rep, self.pub)
                    self.assertEqual(code, 0)
                    self.assert_converged(rep)

    def test_kill_after_each_commit_then_resume(self):
        for k in range(1, 9):
            with self.subTest(kill_after=k):
                rep = self.new_replica()
                slot = os.path.join(self.pub, "slot.json")
                if os.path.exists(slot):
                    os.remove(slot)
                code, _ = run_child(rep, self.pub, kill_after=k)
                self.assertEqual(code, 137)
                # Data and position must agree after every crash.
                pos = read_json(os.path.join(rep, "position.json"))["commit_lsn"]
                self.assertIn(pos, [0] + [m["lsn"] for m in self.log if m["type"] == "commit"])
                code, _ = run_child(rep, self.pub)
                self.assertEqual(code, 0)
                self.assert_converged(rep)

    def test_repeated_reconnects_do_not_duplicate_conflicts(self):
        rep = self.new_replica()
        slot = os.path.join(self.pub, "slot.json")
        for drop in (3, 2, 5, 1, 7, 2, 18, 4, 30, 1):
            if os.path.exists(slot):
                os.remove(slot)
            run_child(rep, self.pub, drop_after=drop)
        code, _ = run_child(rep, self.pub)
        self.assertEqual(code, 0)
        self.assert_converged(rep)
        entries = read_conflicts(os.path.join(rep, "conflicts.jsonl"))
        keys = [(c["commit_lsn"], c["lsn"]) for c in entries]
        self.assertEqual(len(keys), len(set(keys)), "duplicate conflict records")

    def test_stale_non_boundary_position_is_rewound_and_replayed(self):
        """Residue of the old `finally: save(received)` bug must not skip a txn."""
        rep = self.new_replica()
        # Stop cleanly at 4 committed transactions: data and position are both
        # at commit LSN 19.
        code, payload = run_child(rep, self.pub, kill_after=4)
        self.assertEqual(code, 137)
        self.assertEqual(read_json(os.path.join(rep, "position.json")),
                         {"commit_lsn": 19})
        # The old subscriber's `finally` then stored the LSN of the next
        # message it had merely *received* (lsn 20, a `begin`).
        with open(os.path.join(rep, "position.json"), "w", encoding="utf-8") as fh:
            json.dump({"commit_lsn": 20}, fh)
        code, payload = run_child(rep, self.pub)
        self.assertEqual(code, 0, payload)
        self.assert_converged(rep)
        self.assertEqual(payload["result"]["position"], self.model.position)

    def test_second_run_applies_nothing(self):
        rep = self.new_replica()
        run_child(rep, self.pub)
        code, payload = run_child(rep, self.pub)
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"]["applied"], 0)
        self.assert_converged(rep)


class InProcessTests(unittest.TestCase):
    """Direct checks on the applier that the stream protocol cannot provoke."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_ddl_is_ordered_with_data_changes(self):
        from apply import Applier, Replica

        log = [
            {"lsn": 1, "type": "begin", "xid": 1},
            {"lsn": 2, "type": "ddl", "xid": 1, "op": "create_table", "table": "t",
             "primary_key": "id", "columns": {"id": None, "a": None}},
            {"lsn": 3, "type": "insert", "xid": 1, "table": "t", "row": {"id": 1, "a": "x"}},
            # add_column between two updates: the first update must NOT get the
            # column, the second must, and the default must reach old rows.
            {"lsn": 4, "type": "update", "xid": 1, "table": "t", "key": 1,
             "row": {"id": 1, "a": "y"}},
            {"lsn": 5, "type": "ddl", "xid": 1, "op": "add_column", "table": "t",
             "column": "b", "default": 7},
            {"lsn": 6, "type": "update", "xid": 1, "table": "t", "key": 1,
             "row": {"id": 1, "a": "z", "b": 9}},
            {"lsn": 7, "type": "ddl", "xid": 1, "op": "drop_column", "table": "t", "column": "a"},
            {"lsn": 8, "type": "commit", "xid": 1},
        ]
        replica = Replica(self.tmp)
        applier = Applier(replica)
        for message in log[:-1]:
            self.assertIsNone(applier.receive(message))
            # Nothing is visible before commit.
            self.assertEqual(replica.tables, {})
        self.assertEqual(applier.receive(log[-1]), 8)
        self.assertEqual(replica.tables["t"]["columns"], {"id": None, "b": 7})
        self.assertEqual(sorted(replica.tables["t"]["rows"].values(),
                                key=lambda r: r["id"]), [{"id": 1, "b": 9}])

    def test_duplicate_transaction_is_skipped_without_new_conflicts(self):
        from apply import Applier, Replica

        log = [
            {"lsn": 1, "type": "begin", "xid": 1},
            {"lsn": 2, "type": "ddl", "xid": 1, "op": "create_table", "table": "t",
             "primary_key": "id", "columns": {"id": None, "v": None}},
            {"lsn": 3, "type": "insert", "xid": 1, "table": "t", "row": {"id": 1, "v": "a"}},
            {"lsn": 4, "type": "insert", "xid": 1, "table": "t", "row": {"id": 1, "v": "b"}},
            {"lsn": 5, "type": "delete", "xid": 1, "table": "t", "key": 99},
            {"lsn": 6, "type": "commit", "xid": 1},
        ]
        replica = Replica(self.tmp)
        applier = Applier(replica)
        for message in log:
            applier.receive(message)
        self.assertEqual(replica.position, 6)
        self.assertEqual([c["type"] for c in replica.conflicts],
                         ["insert_exists", "delete_missing"])

        # The publisher re-delivers the same transaction after a reconnect.
        replay = Applier(Replica(self.tmp))
        for message in log:
            self.assertIsNone(replay.receive(message))
        self.assertEqual(replica.conflicts, read_conflicts(
            os.path.join(self.tmp, "conflicts.jsonl")))
        self.assertEqual(len(replica.conflicts), 2)  # exactly once, no duplicates
        self.assertEqual(read_json(os.path.join(self.tmp, "position.json")),
                         {"commit_lsn": 6})
        self.assertEqual(replica.tables["t"]["rows"], {"1": {"id": 1, "v": "b"}})

    def test_uncommitted_transaction_is_never_visible(self):
        from apply import Applier, Replica

        replica = Replica(self.tmp)
        applier = Applier(replica)
        applier.receive({"lsn": 1, "type": "begin", "xid": 1})
        applier.receive({"lsn": 2, "type": "ddl", "xid": 1, "op": "create_table",
                         "table": "t", "primary_key": "id", "columns": {"id": None}})
        applier.receive({"lsn": 3, "type": "insert", "xid": 1, "table": "t",
                         "row": {"id": 1}})
        # Connection drops here: on-disk state must still be the empty replica.
        self.assertEqual(read_json(os.path.join(self.tmp, "replica.json")),
                         {"tables": {}})
        self.assertEqual(read_json(os.path.join(self.tmp, "position.json")),
                         {"commit_lsn": 0})

    def test_conflict_log_survives_crash_between_commit_and_position(self):
        from apply import Applier, Replica

        replica = Replica(self.tmp)
        applier = Applier(replica)
        applier.receive({"lsn": 1, "type": "begin", "xid": 1})
        applier.receive({"lsn": 2, "type": "ddl", "xid": 1, "op": "create_table",
                         "table": "t", "primary_key": "id", "columns": {"id": None}})
        applier.receive({"lsn": 3, "type": "delete", "xid": 1, "table": "t", "key": 1})
        applier.receive({"lsn": 4, "type": "commit", "xid": 1})
        # Simulate dying before position.json could be refreshed.
        os.remove(os.path.join(self.tmp, "position.json"))
        changed = Replica(self.tmp)  # startup heals views from the journal
        self.assertEqual(changed.position, 4)
        self.assertEqual(read_json(os.path.join(self.tmp, "position.json")),
                         {"commit_lsn": 4})
        self.assertEqual(read_conflicts(os.path.join(self.tmp, "conflicts.jsonl")),
                         [{"commit_lsn": 4, "key": 1, "lsn": 3,
                           "table": "t", "type": "delete_missing"}])



if __name__ == "__main__":
    unittest.main()
