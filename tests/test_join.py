import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDERS = [{"oid": 1, "cid": 10}, {"oid": 2, "cid": 11}, {"oid": 3, "cid": 10}, {"oid": 4, "cid": 99}]
CUSTOMERS = [{"id": 10, "name": "ann"}, {"id": 11, "name": "bo"}, {"id": 12, "name": "cy"}]
LCOL, RCOL = "cid", "id"
ALL_TYPES = ("inner", "left", "right", "semi", "anti")


def run_join(left, right, kind, work_mem=10000, fanout=8, max_depth=4,
             tmpdir=None, stats=False):
    with tempfile.TemporaryDirectory() as d:
        paths = [os.path.join(d, n) for n in ("l.jsonl", "r.jsonl", "o.jsonl")]
        for path, rows in zip(paths, (left, right)):
            with open(path, "w") as fh:
                fh.writelines(json.dumps(r) + "\n" for r in rows)
        cmd = [sys.executable, os.path.join(ROOT, "join.py"), *paths,
               "--type", kind, "--on", "cid=id",
               "--work-mem", str(work_mem), "--fanout", str(fanout),
               "--max-depth", str(max_depth)]
        stats_path = os.path.join(d, "stats.json")
        if stats:
            cmd += ["--stats", stats_path]
        if tmpdir is not None:
            cmd += ["--tmpdir", tmpdir]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = []
        if os.path.exists(paths[2]):
            with open(paths[2]) as fh:
                out = [json.loads(line) for line in fh]
        st = None
        if stats and os.path.exists(stats_path):
            with open(stats_path) as fh:
                st = json.load(fh)
        return proc, out, st


def write_jsonl(path, rows):
    with open(path, "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)


# -- reference semantics -----------------------------------------------------

def ref_join(left, right, kind):
    """Plain-Python reference for the documented join semantics."""
    def key(row, col):
        v = row.get(col)
        if isinstance(v, bool):
            return ("bad", v)
        return v

    out = []
    if kind == "inner":
        for l in left:
            for r in right:
                lk, rk = key(l, LCOL), key(r, RCOL)
                if lk is not None and lk == rk and type(lk) is type(rk):
                    out.append(dict(l=l, r=r))
    elif kind == "left":
        for l in left:
            lk = key(l, LCOL)
            hit = False
            if lk is not None:
                for r in right:
                    rk = key(r, RCOL)
                    if rk is not None and lk == rk and type(lk) is type(rk):
                        out.append(dict(l=l, r=r))
                        hit = True
            if not hit:
                out.append(dict(l=l, r=None))
    elif kind == "right":
        for r in right:
            rk = key(r, RCOL)
            hit = False
            if rk is not None:
                for l in left:
                    lk = key(l, LCOL)
                    if lk is not None and lk == rk and type(lk) is type(rk):
                        out.append(dict(l=l, r=r))
                        hit = True
            if not hit:
                out.append(dict(l=None, r=r))
    elif kind == "semi":
        for l in left:
            lk = key(l, LCOL)
            if lk is not None and any(
                (rk := key(r, RCOL)) is not None and lk == rk
                and type(lk) is type(rk) for r in right
            ):
                out.append(l)
    elif kind == "anti":
        for l in left:
            lk = key(l, LCOL)
            if lk is None or not any(
                (rk := key(r, RCOL)) is not None and lk == rk
                and type(lk) is type(rk) for r in right
            ):
                out.append(l)
    return out


def canon(rows):
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def partition_of(level, key, fanout):
    digest = hashlib.sha1(
        (str(level) + ":" + json.dumps(key, ensure_ascii=False)).encode()
    ).hexdigest()
    return int(digest, 16) % fanout


def expected_spill(build_rows, work_mem, fanout, max_depth):
    """Simulate the documented partitioning; returns (spilled, depth, abort)."""
    non_null = [r[RCOL] for r in build_rows if r.get(RCOL) is not None]

    def recurse(keys, level):
        spilled = 0
        deepest = 0
        buckets = [[] for _ in range(fanout)]
        for k in keys:
            buckets[partition_of(level, k, fanout)].append(k)
        for bucket in buckets:
            if not bucket:
                continue
            spilled += 1
            deepest = max(deepest, level)
            if len(bucket) > work_mem:
                if level >= max_depth:
                    return None, None, True
                s2, d2, abort = recurse(bucket, level + 1)
                if abort:
                    return None, None, True
                spilled += s2
                deepest = max(deepest, d2)
        return spilled, deepest, False

    if len(non_null) <= work_mem:
        return 0, 0, False
    return recurse(non_null, 1)


# -- datasets ----------------------------------------------------------------

def skew_dataset():
    """Heavy, splittable skew: distinct heavy keys share a level-1 bucket
    but separate at level 2, alongside ordinary keys, strings and NULLs."""
    rng = random.Random(42)
    fanout = 8
    heavy = _co_located_keys(level=1, count=4, fanout=fanout, spread_at=2)
    right, left = [], []
    # The shared level-1 bucket holds 4*60=240 > work_mem=100; after the
    # level-2 split each key sits alone in a 60-row bucket that fits.
    for h in heavy:
        for i in range(60):
            right.append(dict(id=h, tag="b%d-%d" % (h, i)))
    for k in (1, 2, 3, 4, 5):
        for i in range(30):
            right.append(dict(id=k, tag="b%d-%d" % (k, i)))
    for i in range(120):
        right.append(dict(id=None, tag="nullb%d" % i))
    right.append(dict(tag="missing-col"))
    for h in heavy:
        for i in range(100):
            left.append(dict(cid=h, oid=h * 10 + i))
    for k in (2, 4, 6):
        for i in range(50):
            left.append(dict(cid=k, oid=2000 + 100 * k + i))
    for i in range(90):
        left.append(dict(cid=None, oid=3000 + i))
    left.append(dict(oid=4000))
    for i in range(25):
        left.append(dict(cid="7", oid=5000 + i))  # string never == int
    rng.shuffle(right)
    rng.shuffle(left)
    return left, right


def _co_located_keys(level, count, fanout, spread_at):
    """Pick ``count`` keys sharing one bucket at ``level`` but landing in
    distinct buckets at level ``spread_at`` (so a recursive split shrinks)."""
    rng = random.Random(7)
    anchor = partition_of(level, 700000, fanout)
    spread_buckets = set()
    keys = []
    for _ in range(200000):
        candidate = rng.randrange(1, 10 ** 9)
        if partition_of(level, candidate, fanout) != anchor:
            continue
        bucket = partition_of(spread_at, candidate, fanout)
        if bucket in spread_buckets:
            continue
        spread_buckets.add(bucket)
        keys.append(candidate)
        if len(keys) == count:
            return keys
    raise AssertionError("cannot find %d co-located keys" % count)


def all_null_dataset():
    right = [dict(id=None, tag="r%d" % i) for i in range(300)]
    right += [dict(tag="r-missing-%d" % i) for i in range(20)]
    left = [dict(cid=None, oid=i) for i in range(250)]
    left += [dict(oid=900 + i) for i in range(10)]
    left += [dict(cid=1, oid=1000)]  # no build key matches anyway
    return left, right


def empty_build_dataset():
    right = []
    left = [dict(cid=k, oid=k) for k in range(50)]
    left += [dict(cid=None, oid=999)]
    return left, right


def _chained_keys(count, fanout, work_mem, base=900000):
    """Keys sharing a bucket at both level 1 and level 2, then separating
    into distinct level-3 buckets whose rows fit inside work_mem."""
    rng = random.Random(11)
    anchor1 = partition_of(1, base, fanout)
    anchor2 = partition_of(2, base, fanout)
    seen3 = set()
    keys = []
    for _ in range(200000):
        candidate = rng.randrange(1, 10 ** 9)
        if partition_of(1, candidate, fanout) != anchor1:
            continue
        if partition_of(2, candidate, fanout) != anchor2:
            continue
        b3 = partition_of(3, candidate, fanout)
        if b3 in seen3:
            continue
        seen3.add(b3)
        keys.append(candidate)
        if len(keys) == count:
            return keys
    raise AssertionError("cannot find %d chained keys" % count)


def multilevel_dataset(fanout=8, work_mem=40, heavy_count=3):
    """Build data that forces a level-3 split and resolves at level 3.

    ``heavy_count`` heavy keys land in one level-1 bucket, stay together in
    one level-2 bucket, then separate at level 3 into buckets each holding
    work_mem-5 rows -- small enough to build in memory there.
    """
    heavy = _chained_keys(heavy_count, fanout, work_mem)
    per_key = work_mem - 5  # level-3 buckets fit; the shared parents do not
    right = []
    for j, h in enumerate(heavy):
        for i in range(per_key):
            right.append(dict(id=h, tag="h%d-%d" % (j, i)))
    for k in range(1, 40):
        right.append(dict(id=k, tag="d%d" % k))
    left = [dict(cid=h, oid=100 * (j + 1) + i)
            for j, h in enumerate(heavy) for i in range(5)]
    left += [dict(cid=999999, oid=400)]
    left += [dict(cid=None, oid=500)]
    return left, right


def _key_in_other_bucket(level, forbidden, fanout, seed):
    rng = random.Random(seed)
    while True:
        candidate = rng.randrange(1, 10 ** 9)
        if partition_of(level, candidate, fanout) != forbidden:
            return candidate


class JoinTests(unittest.TestCase):
    def test_inner(self):
        out = run_join(ORDERS, CUSTOMERS, "inner")[1]
        self.assertEqual(sorted((r["l"]["oid"], r["r"]["name"]) for r in out),
                         [(1, "ann"), (2, "bo"), (3, "ann")])

    def test_left_keeps_unmatched(self):
        out = run_join(ORDERS, CUSTOMERS, "left")[1]
        self.assertEqual(len(out), 4)
        self.assertIn(dict(l=dict(oid=4, cid=99), r=None), out)

    def test_integer_and_string_keys_differ(self):
        self.assertEqual(run_join([dict(oid=1, cid="10")], CUSTOMERS, "inner")[1], [])

    def test_right_outer_and_null_keys_in_memory(self):
        left = [dict(cid=None, oid=1), dict(cid=10, oid=2)]
        right = [dict(id=None, name="x"), dict(id=10, name="ann"), dict(id=77, name="z")]
        out = run_join(left, right, "right", work_mem=100)[1]
        self.assertIn(dict(l=dict(cid=10, oid=2), r=dict(id=10, name="ann")), out)
        self.assertIn(dict(l=None, r=dict(id=None, name="x")), out)
        self.assertIn(dict(l=None, r=dict(id=77, name="z")), out)
        self.assertEqual(len(out), 3)

    def test_null_does_not_match_null_in_memory(self):
        rows = [dict(cid=None, oid=1), dict(cid=None, oid=2)]
        self.assertEqual(run_join(rows, [dict(id=None)], "inner", work_mem=100)[1], [])
        semi = run_join(rows, [dict(id=None)], "semi", work_mem=100)[1]
        self.assertEqual(semi, [])
        anti = run_join(rows, [dict(id=None)], "anti", work_mem=100)[1]
        self.assertEqual(canon(anti), canon(rows))


class InMemoryJoinTests(unittest.TestCase):
    def test_memory_quota_counts_only_non_null_build_rows(self):
        from hashjoin import InMemoryJoin, MemoryExceeded
        emitted = []
        joiner = InMemoryJoin("inner", "cid", "id", 2, emitted.append)
        right = [dict(id=None), dict(id=1), dict(id=2)]
        joiner.run(right, [dict(cid=1)])
        self.assertEqual(emitted, [dict(l=dict(cid=1), r=dict(id=1))])
        joiner = InMemoryJoin("inner", "cid", "id", 1, emitted.append)
        with self.assertRaises(MemoryExceeded):
            joiner.run([dict(id=1), dict(id=2)], [])


class _ScenarioMixin:
    work_mem = 40
    fanout = 8
    max_depth = 4

    def scenario(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def _check(self, kind, expect_stats):
        left, right = self.scenario()
        with tempfile.TemporaryDirectory() as scratch:
            spill_dir = os.path.join(scratch, "spill")
            os.mkdir(spill_dir)
            proc, out, stats = run_join(
                left, right, kind, work_mem=self.work_mem,
                fanout=self.fanout, max_depth=self.max_depth,
                tmpdir=spill_dir, stats=True
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(canon(out), canon(ref_join(left, right, kind)))
            if expect_stats:
                exp_spill, exp_depth, abort = expected_spill(
                    right, self.work_mem, self.fanout, self.max_depth
                )
                self.assertFalse(abort)
                self.assertEqual(stats["spilled_partitions"], exp_spill)
                self.assertEqual(stats["max_depth"], exp_depth)
                self.assertEqual(stats["build_rows"], len(right))
                self.assertEqual(stats["probe_rows"], len(left))
            else:
                self.assertEqual(stats["spilled_partitions"], 0)
                self.assertEqual(stats["max_depth"], 0)
            # Temporary files are removed whatever the result.
            self.assertEqual(os.listdir(spill_dir), [])

    def test_all_join_types(self):
        for kind in ALL_TYPES:
            with self.subTest(kind=kind):
                self._check(kind, self.expect_stats)


class SkewTests(_ScenarioMixin, unittest.TestCase):
    """Skewed key forces spilling; every join type checked row by row."""
    work_mem = 100
    expect_stats = True

    def scenario(self):
        return skew_dataset()


class AllNullTests(_ScenarioMixin, unittest.TestCase):
    """All keys NULL: nothing is hashable, nothing spills, no NULL=NULL join."""
    work_mem = 10
    expect_stats = False

    def scenario(self):
        return all_null_dataset()


class EmptyBuildTests(_ScenarioMixin, unittest.TestCase):
    """Empty build side: in-memory path, outer fills only."""
    work_mem = 10
    expect_stats = False

    def scenario(self):
        return empty_build_dataset()


class MultilevelRecursionTests(_ScenarioMixin, unittest.TestCase):
    """Skew survives level 1 and level 2; recursion reaches depth 3."""
    work_mem = 40
    fanout = 8
    max_depth = 4
    expect_stats = True

    def scenario(self):
        return multilevel_dataset(self.fanout, self.work_mem)

    def test_recursion_depth_is_three(self):
        left, right = self.scenario()
        exp_spill, exp_depth, abort = expected_spill(
            right, self.work_mem, self.fanout, self.max_depth
        )
        self.assertFalse(abort)
        self.assertEqual(exp_depth, 3)
        self.assertGreater(exp_spill, self.fanout)  # partitions at several levels
        proc, out, stats = run_join(
            left, right, "inner", work_mem=self.work_mem,
            fanout=self.fanout, max_depth=self.max_depth, stats=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(stats["max_depth"], 3)
        self.assertEqual(stats["spilled_partitions"], exp_spill)
        self.assertEqual(canon(out), canon(ref_join(left, right, "inner")))


class DepthLimitTests(unittest.TestCase):
    def test_abort_exit_3_when_partition_never_shrinks(self):
        # A single distinct key repeated past work_mem cannot be split into
        # smaller buckets, so max-depth must abort with status 3.
        right = [dict(id=42, tag="r%d" % i) for i in range(30)]
        left = [dict(cid=42, oid=1)]
        proc, out, stats = run_join(left, right, "inner", work_mem=10,
                                    fanout=8, max_depth=2, stats=True)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIsNone(stats)  # no stats file on abort
        self.assertEqual(out, [])

    def test_abort_when_depth_budget_too_small_for_multilevel(self):
        left, right = multilevel_dataset(fanout=8, work_mem=40)
        proc, _out, _stats = run_join(left, right, "inner", work_mem=40,
                                      fanout=8, max_depth=2)
        self.assertEqual(proc.returncode, 3, proc.stderr)


class FanoutTwoTests(_ScenarioMixin, unittest.TestCase):
    """fanout=2: the smallest legal fanout still spills and recurses."""
    work_mem = 40
    fanout = 2
    max_depth = 6
    expect_stats = True
    heavy_count = 2

    def scenario(self):
        return multilevel_dataset(self.fanout, self.work_mem, self.heavy_count)


class ProbeOnlyPartitionTests(unittest.TestCase):
    def test_probe_rows_without_build_partition_get_outer_fills(self):
        # Build keys all hash into one bucket; probe keys are chosen from a
        # different bucket, so their partition has a probe file but no build
        # file. left/anti must still emit every such row.
        fanout = 8
        # Four distinct build keys share a level-1 bucket (8 rows total, over
        # work_mem=5) but separate at level 2 into buckets of 2 rows, so the
        # spill resolves without hitting max-depth.
        build_keys = _co_located_keys(1, 4, fanout, 2)
        forbidden = partition_of(1, build_keys[0], fanout)
        other = _key_in_other_bucket(1, forbidden, fanout, seed=23)
        right = [dict(id=k, tag="r%d-%d" % (k, j))
                 for k in build_keys for j in range(2)]
        left = [dict(cid=other + i, oid=i) for i in range(20)]
        left += [dict(cid=k, oid=900 + j) for j, k in enumerate(build_keys)]
        proc, out, stats = run_join(left, right, "left", work_mem=5,
                                    fanout=fanout, stats=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # One level-1 build bucket, then four level-2 child buckets.
        self.assertEqual(stats["spilled_partitions"], 5)
        self.assertEqual(stats["max_depth"], 2)
        self.assertEqual(canon(out), canon(ref_join(left, right, "left")))
        proc, out, _ = run_join(left, right, "anti", work_mem=5, fanout=fanout)
        self.assertEqual(canon(out), canon(ref_join(left, right, "anti")))
        proc, out, _ = run_join(left, right, "semi", work_mem=5, fanout=fanout)
        self.assertEqual(canon(out), canon(ref_join(left, right, "semi")))


class AbortCleanupTests(unittest.TestCase):
    def test_temp_files_removed_on_depth_abort(self):
        right = [dict(id=42, tag="r%d" % i) for i in range(30)]
        left = [dict(cid=42, oid=1)]
        with tempfile.TemporaryDirectory() as scratch:
            spill_dir = os.path.join(scratch, "spill")
            os.mkdir(spill_dir)
            proc, _out, _ = run_join(left, right, "inner", work_mem=10,
                                     fanout=8, max_depth=2, tmpdir=spill_dir)
            self.assertEqual(proc.returncode, 3)
            self.assertEqual(os.listdir(spill_dir), [])


class RandomizedDifferentialTests(unittest.TestCase):
    def test_many_random_joins_match_reference_and_stats(self):
        rng = random.Random(2026)
        for trial in range(24):
            kind = ALL_TYPES[trial % len(ALL_TYPES)]
            fanout = rng.choice((2, 3, 8))
            work_mem = rng.choice((3, 7, 25))
            n_right = rng.randrange(1, 120)
            n_left = rng.randrange(1, 120)
            key_pool = list(range(12)) + ["s%d" % i for i in range(6)] + [None]

            def make(n, col):
                rows = []
                for i in range(n):
                    if rng.random() < 0.15:
                        rows.append(dict(tag="%s-missing-%d" % (col, i)))
                    else:
                        k = rng.choice(key_pool)
                        row = dict(tag="%s-%d" % (col, i))
                        if k is not None:
                            row[col] = k
                        else:
                            row[col] = None
                        rows.append(row)
                return rows

            right = make(n_right, RCOL)
            left = make(n_left, LCOL)
            exp_spill, exp_depth, abort = expected_spill(
                right, work_mem, fanout, max_depth=4
            )
            proc, out, stats = run_join(
                left, right, kind, work_mem=work_mem, fanout=fanout,
                max_depth=4, stats=True
            )
            if abort:
                self.assertEqual(proc.returncode, 3, proc.stderr)
                continue
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(canon(out), canon(ref_join(left, right, kind)))
            self.assertEqual(stats["spilled_partitions"], exp_spill)
            self.assertEqual(stats["max_depth"], exp_depth)
            self.assertEqual(stats["build_rows"], n_right)
            self.assertEqual(stats["probe_rows"], n_left)


class ExampleDataTests(unittest.TestCase):
    def _load(self, name):
        with open(os.path.join(ROOT, "examples", name)) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_example_files_every_type_spilled_and_in_memory(self):
        left = self._load("orders.jsonl")
        right = self._load("customers.jsonl")
        for kind in ALL_TYPES:
            for work_mem in (500, 10000):  # spilled path, then in-memory path
                proc, out, stats = run_join(
                    left, right, kind, work_mem=work_mem, fanout=8, stats=True
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(canon(out), canon(ref_join(left, right, kind)))
                if work_mem == 500:
                    self.assertGreater(stats["spilled_partitions"], 0)
                    self.assertGreaterEqual(stats["max_depth"], 1)
                else:
                    self.assertEqual(stats["spilled_partitions"], 0)
                    self.assertEqual(stats["max_depth"], 0)
                self.assertEqual(stats["build_rows"], len(right))
                self.assertEqual(stats["probe_rows"], len(left))


class MemoryKillTests(unittest.TestCase):
    def test_exit_137_when_in_memory_table_exceeds_quota(self):
        # The partitioned path must never call the in-memory operator with
        # more than work_mem rows; a hand-built InMemoryJoin still enforces
        # the quota defensively and reports it as exit status 137.
        from hashjoin import InMemoryJoin, MemoryExceeded
        with self.assertRaises(MemoryExceeded):
            InMemoryJoin("inner", LCOL, RCOL, 1, lambda _v: None).run(
                [dict(id=1), dict(id=2)], []
            )
        # NULL build rows do not count against the quota: all-NULL build of
        # any size stays in memory.
        emitted = []
        InMemoryJoin("right", LCOL, RCOL, 1, emitted.append).run(
            [dict(id=None), dict(tag="missing")], []
        )
        self.assertEqual(len(emitted), 2)


class BadInputTests(unittest.TestCase):
    def test_bad_key_type_exits_2(self):
        proc, _out, _ = run_join([dict(cid=1.5)], [dict(id=1)], "inner")
        self.assertEqual(proc.returncode, 2)
        proc, _out, _ = run_join([dict(cid=1)], [dict(id=True)], "inner")
        self.assertEqual(proc.returncode, 2)

    def test_bad_arguments_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            paths = [os.path.join(d, n) for n in ("l.jsonl", "r.jsonl", "o.jsonl")]
            for p in paths[:2]:
                write_jsonl(p, [])
            base = [sys.executable, os.path.join(ROOT, "join.py"), *paths,
                    "--type", "inner", "--on", "cid=id"]
            self.assertEqual(subprocess.run(base + ["--work-mem", "0"]).returncode, 2)
            self.assertEqual(subprocess.run(base + ["--fanout", "1"]).returncode, 2)
            self.assertEqual(subprocess.run(
                [sys.executable, os.path.join(ROOT, "join.py"), *paths,
                 "--type", "inner", "--on", "cid"]).returncode, 2)


if __name__ == "__main__":
    unittest.main()
