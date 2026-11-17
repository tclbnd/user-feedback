import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import restore  # noqa: E402
import wal  # noqa: E402

CLUSTER = os.path.join(ROOT, "examples", "cluster")


class PitrTests(unittest.TestCase):
    def test_reads_all_segments(self):
        records = wal.read(os.path.join(CLUSTER, "wal"))
        self.assertEqual(sorted(records), list(range(1, 34)))

    def test_restore_to_end_of_log(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "db")
            proc = subprocess.run([sys.executable, os.path.join(ROOT, "pitr.py"), "restore", CLUSTER, out,
                                   "--to-time", "5000"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            with open(os.path.join(out, "data.json")) as fh:
                tables = json.load(fh)["tables"]
            self.assertEqual(tables["accounts"]["alice"], "75")
            self.assertEqual(tables["accounts"]["erin"], "10")
            self.assertNotIn("orders", tables)

    def test_example_cluster_at_every_replayable_time(self):
        """Restores from the shipped fuzzy backups match a full-log replay."""
        records = wal.read(os.path.join(CLUSTER, "wal"))
        writes, commits = {}, {}
        for lsn in sorted(records):
            rec = records[lsn]
            if rec["type"] in ("PUT", "DEL"):
                writes.setdefault(rec["tx"], []).append(rec)
            elif rec["type"] == "COMMIT":
                commits[rec["tx"]] = rec["time"]
        with tempfile.TemporaryDirectory() as d:
            for target in (1120, 1130, 1150, 1160, 1200, 1210, 1220,
                           1250, 1300, 1330, 2000):
                with self.subTest(target=target):
                    out = os.path.join(d, "db-%d" % target)
                    proc = subprocess.run(
                        [sys.executable, os.path.join(ROOT, "pitr.py"),
                         "restore", CLUSTER, out, "--to-time", str(target)],
                        capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    with open(os.path.join(out, "data.json")) as fh:
                        tables = json.load(fh)["tables"]
                    visible = {name: rows for name, rows in tables.items() if rows}
                    # Replaying the whole log up to the target is the truth,
                    # whichever base backup was chosen.
                    state = {}
                    for tx, _time in sorted(commits.items(), key=lambda item: item[1]):
                        if _time > target:
                            continue
                        for write in writes.get(tx, ()):
                            rows = state.setdefault(write["table"], {})
                            if write["type"] == "PUT":
                                rows[write["key"]] = write["value"]
                            else:
                                rows.pop(write["key"], None)
                    state = {name: rows for name, rows in state.items() if rows}
                    self.assertEqual(visible, state)


# A timestamped write stream.  Times strictly increase; segment files break
# inside open transactions so replay cannot rely on segment boundaries.
RECORDS = [
    (1, 100, 1, "BEGIN", None, None, None),
    (2, 101, 1, "PUT", "accounts", "alice", "100"),
    (3, 102, 1, "PUT", "accounts", "bob", "50"),
    (4, 110, 1, "COMMIT", None, None, None),
    (5, 120, 2, "BEGIN", None, None, None),
    (6, 121, 2, "PUT", "accounts", "alice", "90"),
    (7, 130, 3, "BEGIN", None, None, None),
    (8, 131, 3, "PUT", "orders", "o1", "alice:10"),
    (9, 140, 2, "COMMIT", None, None, None),
    (10, 141, 3, "PUT", "accounts", "carol", "70"),
    (11, 150, 3, "COMMIT", None, None, None),
    (12, 160, 4, "BEGIN", None, None, None),
    (13, 161, 4, "DEL", "accounts", "bob", None),
    (14, 170, 4, "COMMIT", None, None, None),
    (15, 180, 5, "BEGIN", None, None, None),
    (16, 181, 5, "PUT", "accounts", "bob", "55"),
    (17, 190, 5, "COMMIT", None, None, None),
]
SEGMENTS = [(1, 6), (7, 12), (13, 17)]

# A second stream where transactions overlap: tx2 writes before tx1 commits,
# and tx3 aborts.  Commits must be applied in commit order, not in the order
# the writes appear in the log.
OVERLAP = [
    (1, 200, 1, "BEGIN", None, None, None),
    (2, 201, 1, "PUT", "kv", "x", "1"),
    (3, 202, 2, "BEGIN", None, None, None),
    (4, 203, 2, "PUT", "kv", "x", "2"),
    (5, 204, 2, "COMMIT", None, None, None),
    (6, 205, 1, "COMMIT", None, None, None),
    (7, 206, 3, "BEGIN", None, None, None),
    (8, 207, 3, "PUT", "kv", "y", "3"),
    (9, 208, 3, "ABORT", None, None, None),
    (10, 209, 4, "BEGIN", None, None, None),
    (11, 210, 4, "PUT", "kv", "z", "4"),
    (12, 211, 4, "COMMIT", None, None, None),
]
OVERLAP_BACKUPS = {
    "base0": {
        "manifest": {"start_lsn": 0, "start_time": 199, "redo_lsn": 1,
                     "end_lsn": 0, "end_time": 199},
        "data": {"tables": {"kv": {}}},
    },
}

# base0 finished exactly at the tx1 commit.  base1 was taken while tx3 and tx4
# were committing, so its copy happens to hold tx2 and tx3 but not tx4.
BACKUPS = {
    "base0": {
        "manifest": {"start_lsn": 4, "start_time": 109, "redo_lsn": 5,
                     "end_lsn": 4, "end_time": 110},
        "data": {"tables": {"accounts": {"alice": "100", "bob": "50"}}},
    },
    "base1": {
        "manifest": {"start_lsn": 9, "start_time": 140, "redo_lsn": 7,
                     "end_lsn": 14, "end_time": 170},
        "data": {"tables": {
            "accounts": {"alice": "90", "carol": "70"},
            "orders": {"o1": "alice:10"},
        }},
    },
}


def record_json(rec):
    lsn, time, tx, kind, table, key, value = rec
    out = {"lsn": lsn, "time": time, "tx": tx, "type": kind}
    if kind in ("PUT", "DEL"):
        out["table"] = table
        out["key"] = key
        if kind == "PUT":
            out["value"] = value
    return out


def build_cluster(root, backups=None, segments=None, drop_segment=None,
                  drop_lsn=None, corrupt_lsn=None, truncate_segment=None,
                  records=None):
    backups = BACKUPS if backups is None else backups
    records = RECORDS if records is None else records
    if segments is None:
        segments = SEGMENTS if records is RECORDS else [(records[0][0], records[-1][0])]
    os.makedirs(os.path.join(root, "wal"))
    by_lsn = {rec[0]: rec for rec in records}
    for first, last in segments:
        if drop_segment == (first, last):
            continue
        upper = last - 1 if truncate_segment == (first, last) else last
        lines = []
        for lsn in range(first, upper + 1):
            if lsn == drop_lsn:
                continue
            if lsn == corrupt_lsn:
                lines.append('{"lsn": %d, "time": ' % lsn)
            else:
                lines.append(json.dumps(record_json(by_lsn[lsn])))
        name = "%08d-%08d.jsonl" % (first, last)
        with open(os.path.join(root, "wal", name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    os.makedirs(os.path.join(root, "backups"))
    for name, backup in backups.items():
        path = os.path.join(root, "backups", name)
        os.makedirs(path)
        with open(os.path.join(path, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(backup["manifest"], fh)
        with open(os.path.join(path, "data.json"), "w", encoding="utf-8") as fh:
            json.dump(backup["data"], fh)
    return root


def snapshot_at(target, records=None):
    """Independent oracle: the rows as of a time or a transaction boundary."""
    records = RECORDS if records is None else records
    kind, value = target
    if kind == "time":
        cutoff = value
    else:
        commit_time = next(time for _lsn, time, tx, kind_, *_ in records
                           if kind_ == "COMMIT" and tx == value)
        cutoff = commit_time if kind == "tx" else commit_time - 1
    writes, commits = {}, {}
    for lsn, time, tx, kind_, table, key, value_ in records:
        if kind_ in ("PUT", "DEL"):
            writes.setdefault(tx, []).append((kind_, table, key, value_))
        elif kind_ == "COMMIT" and time <= cutoff:
            commits[tx] = time
    tables = {}
    for tx, _time in sorted(commits.items(), key=lambda item: item[1]):
        for kind_, table, key, value_ in writes.get(tx, ()):
            rows = tables.setdefault(table, {})
            if kind_ == "PUT":
                rows[key] = value_
            else:
                rows.pop(key, None)
    return {name: rows for name, rows in sorted(tables.items()) if rows}


def run_restore(cluster, out, flag, value):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "pitr.py"), "restore", cluster, out,
         flag, str(value)],
        capture_output=True, text=True)


class PITRScenarioTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.cluster = build_cluster(os.path.join(self.dir, "cluster"))
        self.out = os.path.join(self.dir, "db")

    def tearDown(self):
        self._tmp.cleanup()

    def do_restore(self, flag, value, cluster=None, out=None):
        out = out or self.out
        proc = run_restore(cluster or self.cluster, out, flag, value)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(os.path.join(out, "data.json"), encoding="utf-8") as fh:
            return json.loads(proc.stdout), json.load(fh)["tables"]

    def assert_gap(self, proc, missing_from, latest):
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        error = json.loads(proc.stdout)
        self.assertEqual(error["error"], "log gap")
        self.assertEqual(error["missing_from"], missing_from)
        self.assertEqual(error["latest_recoverable_time"], latest)
        return error

    def test_time_targets_match_snapshot_row_by_row(self):
        # Inside a transaction, right on a COMMIT, just before and after it,
        # at the first usable backup, and past the end of the log.
        for target in (110, 111, 120, 130, 140, 141, 149, 150, 151, 160,
                       165, 169, 170, 171, 180, 181, 190, 191, 5000):
            with self.subTest(target=target):
                summary, tables = self.do_restore("--to-time", target)
                self.assertEqual(tables, snapshot_at(("time", target)))
                self.assertLessEqual(
                    summary["cut_lsn"],
                    max(lsn for lsn, time, *_ in RECORDS if time <= target))

    def test_transaction_boundaries(self):
        for tx in (2, 3, 4, 5):
            commit = next(lsn for lsn, _t, n, kind, *_ in RECORDS
                          if kind == "COMMIT" and n == tx)
            with self.subTest(tx=tx, mode="to"):
                summary, tables = self.do_restore("--to-tx", tx)
                self.assertEqual(tables, snapshot_at(("tx", tx)))
                self.assertEqual(summary["cut_lsn"], commit)
            with self.subTest(tx=tx, mode="before"):
                summary, tables = self.do_restore("--before-tx", tx)
                self.assertEqual(tables, snapshot_at(("before", tx)))
                self.assertEqual(summary["cut_lsn"], commit - 1)

    def test_transaction_open_at_target_is_excluded(self):
        # tx4 is open from lsn 12 on; its DEL must not appear at time 165.
        _, tables = self.do_restore("--to-time", 165)
        self.assertEqual(tables["accounts"]["bob"], "50")
        self.assertEqual(tables, snapshot_at(("time", 165)))

    def test_commit_exactly_at_target_time_is_included(self):
        _, tables = self.do_restore("--to-time", 170)
        self.assertNotIn("bob", tables["accounts"])
        _, tables = self.do_restore("--to-time", 169)
        self.assertEqual(tables["accounts"]["bob"], "50")

    def test_backup_taken_during_writes(self):
        # Before base1 finished only base0 may be used; afterwards base1 is
        # used and the commits it partially holds are replayed again.
        summary, tables = self.do_restore("--to-time", 165)
        self.assertEqual(summary["backup"], "base0")
        self.assertEqual(tables, snapshot_at(("time", 165)))
        summary, tables = self.do_restore("--to-time", 175)
        self.assertEqual(summary["backup"], "base1")
        self.assertEqual(tables, snapshot_at(("time", 175)))

    def test_redo_fills_commits_the_fuzzy_backup_missed(self):
        backups = {"base1": {
            "manifest": dict(BACKUPS["base1"]["manifest"]),
            "data": {"tables": {"accounts": {"alice": "90", "bob": "50"}}},
        }}
        cluster = build_cluster(os.path.join(self.dir, "cluster2"), backups=backups)
        summary, tables = self.do_restore("--to-time", 175, cluster=cluster)
        self.assertEqual(summary["backup"], "base1")
        self.assertEqual(tables, snapshot_at(("time", 175)))
        self.assertEqual(tables["accounts"]["carol"], "70")
        self.assertNotIn("bob", tables["accounts"])

    def test_unfinished_backup_is_never_used(self):
        half = {
            "manifest": {"start_lsn": 9, "start_time": 140, "redo_lsn": 7},
            "data": {"tables": {"accounts": {"alice": "bogus"}}},
        }
        backups = {"base0": BACKUPS["base0"], "half": half}
        cluster = build_cluster(os.path.join(self.dir, "cluster3"), backups=backups)
        summary, tables = self.do_restore("--to-time", 175, cluster=cluster)
        self.assertEqual(summary["backup"], "base0")
        self.assertEqual(tables, snapshot_at(("time", 175)))

    def test_gap_reports_latest_recoverable_time(self):
        cluster = build_cluster(os.path.join(self.dir, "gap"), drop_segment=(7, 12))
        proc = run_restore(cluster, os.path.join(self.dir, "gapdb"), "--to-time", 130)
        self.assert_gap(proc, 7, 121)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "gapdb")))

    def test_targets_up_to_last_record_before_gap_still_work(self):
        cluster = build_cluster(os.path.join(self.dir, "gap"), drop_segment=(7, 12))
        out = os.path.join(self.dir, "gapdb")
        _, tables = self.do_restore("--to-time", 121, cluster=cluster, out=out)
        self.assertEqual(tables, snapshot_at(("time", 121)))
        _, tables = self.do_restore("--to-tx", 1, cluster=cluster, out=out)
        self.assertEqual(tables, snapshot_at(("tx", 1)))
        # Anything that may live behind the gap is refused, not guessed.
        for flag, value in (("--to-tx", 2), ("--before-tx", 2), ("--to-time", 122)):
            with self.subTest(flag=flag, value=value):
                self.assert_gap(run_restore(cluster, out, flag, value), 7, 121)

    def test_missing_record_inside_segment_is_a_gap(self):
        cluster = build_cluster(os.path.join(self.dir, "hole"), drop_lsn=8)
        scan = wal.scan(os.path.join(cluster, "wal"))
        self.assertEqual(scan.gap.missing_from, 8)
        self.assertEqual(scan.gap.latest_recoverable_time, 130)
        self.assertEqual(sorted(scan.records), [1, 2, 3, 4, 5, 6, 7])
        proc = run_restore(cluster, os.path.join(self.dir, "holedb"), "--to-time", 165)
        self.assert_gap(proc, 8, 130)

    def test_corrupt_line_is_a_gap(self):
        cluster = build_cluster(os.path.join(self.dir, "bad"), corrupt_lsn=8)
        scan = wal.scan(os.path.join(cluster, "wal"))
        self.assertEqual(scan.gap.missing_from, 8)
        self.assertEqual(scan.gap.latest_recoverable_time, 130)
        self.assertEqual(sorted(scan.records), [1, 2, 3, 4, 5, 6, 7])

    def test_truncated_final_segment_is_a_gap(self):
        cluster = build_cluster(os.path.join(self.dir, "cut"), truncate_segment=(13, 17))
        scan = wal.scan(os.path.join(cluster, "wal"))
        self.assertEqual(scan.gap.missing_from, 17)
        self.assertEqual(scan.gap.latest_recoverable_time, 181)
        proc = run_restore(cluster, os.path.join(self.dir, "cutdb"), "--to-time", 185)
        self.assert_gap(proc, 17, 181)
        _, tables = self.do_restore("--to-time", 181, cluster=cluster,
                                    out=os.path.join(self.dir, "cutdb"))
        self.assertEqual(tables, snapshot_at(("time", 181)))

    def test_trimmed_head_only_blocks_targets_that_need_it(self):
        # The first segment is gone.  Restoring near the end can start from
        # base1 (redo_lsn 7, still present); an earlier target that needs
        # base0's redo records must fail rather than silently skip them.
        cluster = build_cluster(os.path.join(self.dir, "head"),
                                drop_segment=(1, 6))
        out = os.path.join(self.dir, "headdb")
        _, tables = self.do_restore("--to-time", 175, cluster=cluster, out=out)
        self.assertEqual(tables, snapshot_at(("time", 175)))
        proc = run_restore(cluster, out, "--to-time", 165)
        self.assertEqual(proc.returncode, 4, proc.stdout)
        error = json.loads(proc.stdout)
        self.assertEqual(error["error"], "log gap")
        self.assertEqual(error["missing_from"], 5)
        self.assertIsNone(error["latest_recoverable_time"])

    def test_missing_last_segment_is_still_detected(self):
        # Nothing in the remaining segment names advertises records past 12,
        # but base1 recorded end_lsn 14, so the tail is known to be lost.
        cluster = build_cluster(os.path.join(self.dir, "tail"),
                                drop_segment=(13, 17))
        scan = wal.scan(os.path.join(cluster, "wal"))
        self.assertIsNone(scan.gap)
        self.assertEqual(max(scan.records), 12)
        proc = run_restore(cluster, os.path.join(self.dir, "taildb"),
                           "--to-time", 165)
        self.assert_gap(proc, 13, 160)
        # Targets up to the last record before the gap still work.
        out = os.path.join(self.dir, "taildb")
        _, tables = self.do_restore("--to-time", 160, cluster=cluster, out=out)
        self.assertEqual(tables, snapshot_at(("time", 160)))
        # tx4 commits at 170, past the last trustworthy record.
        self.assert_gap(run_restore(cluster, out, "--to-tx", 4), 13, 160)

    def test_restore_swaps_in_only_after_check(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "keep.txt"), "w", encoding="utf-8") as fh:
            fh.write("old")
        self.do_restore("--to-time", 150)
        self.assertFalse(os.path.exists(os.path.join(self.out, "keep.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.out, "data.json")))
        self.assertFalse(os.path.exists(self.out + ".restoring"))
        self.assertFalse(os.path.exists(self.out + ".previous"))

    def test_failed_restore_leaves_existing_copy_untouched(self):
        os.makedirs(self.out)
        old = {"tables": {"precious": {"row": "1"}}}
        with open(os.path.join(self.out, "data.json"), "w", encoding="utf-8") as fh:
            json.dump(old, fh)
        cluster = build_cluster(os.path.join(self.dir, "gap"), drop_segment=(7, 12))
        proc = run_restore(cluster, self.out, "--to-time", 130)
        self.assertEqual(proc.returncode, 4)
        with open(os.path.join(self.out, "data.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), old)
        self.assertFalse(os.path.exists(self.out + ".restoring"))
        self.assertFalse(os.path.exists(self.out + ".previous"))

    def test_restore_check_rejects_bad_copy_before_swap(self):
        os.makedirs(self.out)
        old = {"tables": {"precious": {"row": "1"}}}
        with open(os.path.join(self.out, "data.json"), "w", encoding="utf-8") as fh:
            json.dump(old, fh)

        def write_wrong(obj, fh, **kwargs):
            fh.write('{"tables": {}}')

        with mock.patch.object(restore.json, "dump", write_wrong):
            with self.assertRaises(restore.RestoreError) as caught:
                restore.restore(self.cluster, self.out, ("time", 150),
                                wal.scan(os.path.join(self.cluster, "wal")))
        self.assertEqual(caught.exception.details["error"], "restore check failed")
        with open(os.path.join(self.out, "data.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), old)
        self.assertFalse(os.path.exists(self.out + ".restoring"))

    def test_verify_catches_bad_replay_before_swap(self):
        os.makedirs(self.out)
        old = {"tables": {"precious": {"row": "1"}}}
        with open(os.path.join(self.out, "data.json"), "w", encoding="utf-8") as fh:
            json.dump(old, fh)

        real_replay = restore.replay

        def wrong_replay(tables, records, backup, cut):
            tables = real_replay(tables, records, backup, cut)
            # carol is set by tx3, which commits after the base's start_lsn,
            # so the log alone determines its final value.
            tables["accounts"]["carol"] = "corrupted"
            return tables

        with mock.patch.object(restore, "replay", wrong_replay):
            with self.assertRaises(restore.RestoreError) as caught:
                restore.restore(self.cluster, self.out, ("time", 175),
                                wal.scan(os.path.join(self.cluster, "wal")))
        self.assertEqual(caught.exception.details["error"], "restore check failed")
        self.assertEqual(caught.exception.details["key"], "carol")
        with open(os.path.join(self.out, "data.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), old)
        self.assertFalse(os.path.exists(self.out + ".restoring"))

    def test_install_failure_restores_previous_copy(self):
        os.makedirs(self.out)
        old = {"tables": {"precious": {"row": "1"}}}
        with open(os.path.join(self.out, "data.json"), "w", encoding="utf-8") as fh:
            json.dump(old, fh)

        real_replace = os.replace

        def failing_replace(src, dst):
            # Only the swap of the finished copy fails; the rollback of the
            # previous copy still works.
            if src.endswith(".restoring"):
                raise OSError("disk full")
            return real_replace(src, dst)

        with mock.patch.object(restore.os, "replace", failing_replace):
            with self.assertRaises(restore.RestoreError) as caught:
                restore.restore(self.cluster, self.out, ("time", 175),
                                wal.scan(os.path.join(self.cluster, "wal")))
        self.assertEqual(caught.exception.details["error"], "install failed")
        with open(os.path.join(self.out, "data.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), old)
        self.assertFalse(os.path.exists(self.out + ".restoring"))
        self.assertFalse(os.path.exists(self.out + ".previous"))

    def test_randomized_streams_match_snapshot_at_every_time(self):
        # Deterministic pseudo-random streams: overlapping transactions,
        # aborts, deletes and segment splits inside transactions.  Every
        # record time, the instant just before it and just after it is tried.
        import random

        rng = random.Random(20240117)
        for case in range(8):
            records = []
            lsn, time = 1, 1000
            commits = []
            open_txs = []
            next_tx = 1
            keys = ["k%d" % i for i in range(4)]
            for _step in range(rng.randint(12, 28)):
                action = rng.random()
                if action < 0.22 or not open_txs:
                    tx = next_tx
                    next_tx += 1
                    records.append((lsn, time, tx, "BEGIN", None, None, None))
                    open_txs.append(tx)
                elif action < 0.72:
                    tx = rng.choice(open_txs)
                    key = rng.choice(keys)
                    if rng.random() < 0.3:
                        records.append((lsn, time, tx, "DEL", "t", key, None))
                    else:
                        records.append((lsn, time, tx, "PUT", "t", key,
                                        str(rng.randint(0, 9))))
                elif action < 0.85:
                    tx = rng.choice(open_txs)
                    records.append((lsn, time, tx, "ABORT", None, None, None))
                    open_txs.remove(tx)
                else:
                    tx = rng.choice(open_txs)
                    records.append((lsn, time, tx, "COMMIT", None, None, None))
                    open_txs.remove(tx)
                    commits.append(tx)
                lsn += 1
                time += rng.randint(1, 4)
            for tx in list(open_txs):
                records.append((lsn, time, tx, "COMMIT", None, None, None))
                commits.append(tx)
                lsn += 1
                time += rng.randint(1, 4)
            backups = {
                "base0": {
                    "manifest": {"start_lsn": 0, "start_time": records[0][1] - 1,
                                 "redo_lsn": 1, "end_lsn": 0,
                                 "end_time": records[0][1] - 1},
                    "data": {"tables": {"t": {}}},
                },
            }
            # One backup taken mid-stream, ending right after a commit.
            mid = records[len(records) // 2]
            backups["base1"] = {
                "manifest": {"start_lsn": 0, "start_time": records[0][1] - 1,
                             "redo_lsn": 1, "end_lsn": mid[0],
                             "end_time": mid[1]},
                "data": {"tables": {"t": {}}},
            }
            cluster = build_cluster(os.path.join(self.dir, "rand%d" % case),
                                    backups=backups, records=records)
            out = os.path.join(self.dir, "randout%d" % case)
            scan = wal.scan(os.path.join(cluster, "wal"))
            for _lsn, when, *_ in records:
                for target in (when - 1, when, when + 1):
                    restore.restore(cluster, out, ("time", target), scan)
                    with open(os.path.join(out, "data.json"),
                              encoding="utf-8") as fh:
                        tables = json.load(fh)["tables"]
                    self.assertEqual(tables,
                                     snapshot_at(("time", target),
                                                 records=records),
                                     "case %d target %d" % (case, target))
            for tx in commits:
                restore.restore(cluster, out, ("tx", tx), scan)
                with open(os.path.join(out, "data.json"), encoding="utf-8") as fh:
                    tables = json.load(fh)["tables"]
                self.assertEqual(tables, snapshot_at(("tx", tx), records=records))

    def test_transaction_not_found(self):
        with self.assertRaises(restore.RestoreError) as caught:
            restore.restore(self.cluster, self.out, ("tx", 99),
                            wal.scan(os.path.join(self.cluster, "wal")))
        self.assertEqual(caught.exception.details["error"], "transaction not found")
        self.assertFalse(os.path.exists(self.out))

    def test_no_usable_backup_before_first_finished_backup(self):
        # base0 only finished at time 110, so an earlier target has nothing.
        proc = run_restore(self.cluster, self.out, "--to-time", 105)
        self.assertEqual(proc.returncode, 4, proc.stdout)
        self.assertEqual(json.loads(proc.stdout)["error"], "no usable backup")
        self.assertFalse(os.path.exists(self.out))
        # A transaction cut at or after base0's end does have a usable base.
        summary, _ = self.do_restore("--to-tx", 1)
        self.assertEqual(summary["backup"], "base0")

    def test_overlapping_transactions_apply_in_commit_order(self):
        cluster = build_cluster(os.path.join(self.dir, "overlap"),
                                backups=OVERLAP_BACKUPS, records=OVERLAP)
        out = os.path.join(self.dir, "overlapdb")
        # tx2 commits before tx1 but writes x after tx1; commit order wins.
        _, tables = self.do_restore("--to-tx", 1, cluster=cluster, out=out)
        self.assertEqual(tables["kv"]["x"], "1")
        self.assertEqual(tables, snapshot_at(("tx", 1), records=OVERLAP))
        _, tables = self.do_restore("--to-tx", 2, cluster=cluster, out=out)
        self.assertEqual(tables["kv"]["x"], "2")
        self.assertEqual(tables, snapshot_at(("tx", 2), records=OVERLAP))

    def test_aborted_transaction_leaves_no_rows(self):
        cluster = build_cluster(os.path.join(self.dir, "overlap"),
                                backups=OVERLAP_BACKUPS, records=OVERLAP)
        _, tables = self.do_restore("--to-time", 208, cluster=cluster,
                                    out=os.path.join(self.dir, "overlapdb"))
        self.assertNotIn("y", tables["kv"])
        self.assertEqual(tables, snapshot_at(("time", 208), records=OVERLAP))

    def test_repeated_restores_from_the_same_cluster(self):
        # The same cluster restored to several points, each into its own copy.
        expected = {}
        for target in (130, 150, 175, 190):
            out = os.path.join(self.dir, "copy-%d" % target)
            _, tables = self.do_restore("--to-time", target, out=out)
            expected[target] = tables
            self.assertEqual(tables, snapshot_at(("time", target)))
        # Re-running later does not depend on an earlier output copy.
        shutil.rmtree(os.path.join(self.dir, "copy-175"))
        _, tables = self.do_restore("--to-time", 175,
                                    out=os.path.join(self.dir, "copy-175"))
        self.assertEqual(tables, expected[175])


if __name__ == "__main__":
    unittest.main()
