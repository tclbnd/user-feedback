import functools
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import keys  # noqa: E402


def run_sort(rows, *args):
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.jsonl"), os.path.join(d, "out.jsonl")
        with open(src, "w") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in rows)
        subprocess.run([sys.executable, os.path.join(ROOT, "sort.py"), src, dst, *args], check=True)
        with open(dst) as fh:
            return [json.loads(line) for line in fh]


def run_sort_full(rows, *args):
    """Run the CLI in its own temp directory and return (rows, stats, leftovers)."""
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.jsonl"), os.path.join(d, "out.jsonl")
        stats = os.path.join(d, "stats.json")
        work = os.path.join(d, "scratch")
        os.mkdir(work)
        with open(src, "w") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in rows)
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(ROOT, "sort.py"),
                src,
                dst,
                *args,
                "--stats",
                stats,
                "--tmpdir",
                work,
            ]
        )
        out = []
        if os.path.exists(dst):
            with open(dst) as fh:
                out = [json.loads(line) for line in fh]
        parsed = None
        if os.path.exists(stats):
            with open(stats) as fh:
                parsed = json.load(fh)
        return proc.returncode, out, parsed, sorted(os.listdir(work))


# ---------------------------------------------------------------------------
# Independent reference implementation
#
# Deliberately does not reuse keys.py: it compares rows pairwise from a plain
# reading of the ORDER BY grammar so that a bug in the key encoding cannot be
# mirrored by the oracle.
# ---------------------------------------------------------------------------


def ref_specs(order):
    specs = []
    for part in order.split(","):
        words = part.split()
        col, desc, nulls_first, collate = words[0], False, None, "binary"
        rest = [w.lower() for w in words[1:]]
        i = 0
        while i < len(rest):
            word = rest[i]
            if word in ("asc", "desc"):
                desc = word == "desc"
                i += 1
            elif word == "collate":
                collate = rest[i + 1]
                i += 2
            elif word == "nulls":
                nulls_first = rest[i + 1] == "first"
                i += 2
            else:
                raise AssertionError(word)
        if nulls_first is None:
            nulls_first = desc
        specs.append((col, desc, nulls_first, collate))
    return specs


def ref_compare(specs):
    def cmp(a, b):
        for col, desc, nulls_first, collate in specs:
            x, y = a.get(col), b.get(col)
            if x is None or y is None:
                if x is None and y is None:
                    continue
                if x is None:
                    return -1 if nulls_first else 1
                return 1 if nulls_first else -1
            # Type ordering: numbers, then strings, then everything else.
            def cls(v):
                if isinstance(v, bool):
                    return 2
                if isinstance(v, (int, float)):
                    return 0
                if isinstance(v, str):
                    return 1
                return 3

            if cls(x) != cls(y):
                result = -1 if cls(x) < cls(y) else 1
                return -result if desc else result
            if cls(x) == 1:
                x, y = (x.casefold(), y.casefold()) if collate == "nocase" else (x, y)
            elif cls(x) == 3:
                x, y = repr(x), repr(y)
            if x == y:
                continue
            result = -1 if x < y else 1
            return -result if desc else result
        return 0

    return cmp


def reference_sort(rows, order, limit=None):
    ordered = sorted(rows, key=functools.cmp_to_key(ref_compare(ref_specs(order))))
    return ordered[:limit] if limit is not None else ordered


def ids(rows):
    return [r.get("id") for r in rows]


class SortTests(unittest.TestCase):
    def test_parse_rejects_unknown_collation(self):
        with self.assertRaises(ValueError):
            keys.parse("v COLLATE utf8mb4")
        with self.assertRaises(ValueError):
            keys.parse("v NULLS MAYBE")
        with self.assertRaises(ValueError):
            keys.parse("v COLLATE")
        with self.assertRaises(ValueError):
            keys.parse("v NULLS")

    def test_parse(self):
        k = keys.parse("a, b DESC NULLS last, c collate NOCASE")
        self.assertEqual([x["col"] for x in k], ["a", "b", "c"])
        self.assertTrue(k[1]["desc"])
        self.assertEqual(k[2]["collate"], "nocase")

    def test_numbers_before_strings(self):
        out = run_sort([{"v": "b"}, {"v": 3}, {"v": "a"}, {"v": 1}], "--order", "v")
        self.assertEqual([r["v"] for r in out], [1, 3, "a", "b"])

    def test_in_memory_ascending(self):
        rows = [{"id": i, "v": (i * 7) % 11} for i in range(30)]
        out = run_sort(rows, "--order", "v")
        self.assertEqual([r["v"] for r in out], sorted(r["v"] for r in rows))


class KeyEncodingTests(unittest.TestCase):
    """The key tuple must agree with a from-scratch pairwise comparator."""

    ORDERS = [
        "v",
        "v DESC",
        "v ASC NULLS FIRST",
        "v DESC NULLS LAST",
        "v ASC NULLS LAST",
        "v DESC NULLS FIRST",
    ]

    def check(self, rows, order):
        from keys import row_key

        spec_keys = keys.parse(order)
        got = sorted(rows, key=row_key(spec_keys))
        want = reference_sort(rows, order)
        self.assertEqual(ids(got), ids(want), order)

    def test_null_placement_and_direction(self):
        for order in self.ORDERS:
            with self.subTest(order=order):
                self.check([{"id": i, "v": v} for i, v in enumerate([3, None, 1, 2, None])], order)

    def test_mixed_directions(self):
        rows = [
            {"id": 0, "a": 1, "b": "x"},
            {"id": 1, "a": 1, "b": "y"},
            {"id": 2, "a": 2, "b": "x"},
            {"id": 3, "a": 2, "b": "y"},
            {"id": 4, "a": 2, "b": None},
            {"id": 5, "a": None, "b": "x"},
            {"id": 6, "a": 1, "b": None},
        ]
        for order in (
            "a ASC, b DESC",
            "a DESC, b ASC",
            "a DESC NULLS LAST, b DESC NULLS FIRST",
            "a ASC NULLS FIRST, b ASC NULLS LAST",
        ):
            with self.subTest(order=order):
                self.check(rows, order)

    def test_nocase_equality_is_stable_and_ordered(self):
        from keys import row_key

        rows = [{"id": i, "v": v} for i, v in enumerate(["b", "B", "a", "A", "c"])]
        got = [r["id"] for r in sorted(rows, key=row_key(keys.parse("v COLLATE nocase")))]
        self.assertEqual(got, [2, 3, 0, 1, 4])

    def test_descending_does_not_reverse_nulls(self):
        from keys import row_key

        rows = [{"id": 0, "v": 1}, {"id": 1, "v": None}, {"id": 2, "v": 2}]
        a = [r["id"] for r in sorted(rows, key=row_key(keys.parse("v DESC")))]
        self.assertEqual(a, [1, 2, 0])  # DESC defaults to NULLS FIRST
        b = [r["id"] for r in sorted(rows, key=row_key(keys.parse("v DESC NULLS LAST")))]
        self.assertEqual(b, [2, 0, 1])

    def test_descending_strings(self):
        from keys import row_key

        rows = [{"id": i, "v": v} for i, v in enumerate(["a", "c", "b"])]
        got = [r["id"] for r in sorted(rows, key=row_key(keys.parse("v DESC")))]
        self.assertEqual(got, [1, 2, 0])

    def test_nan_sorts_after_numbers(self):
        from keys import row_key

        rows = [{"id": i, "v": v} for i, v in enumerate([math.nan, 3, -1])]
        got = [r["id"] for r in sorted(rows, key=row_key(keys.parse("v")))]
        self.assertEqual(got, [2, 1, 0])


class ExternalSortTests(unittest.TestCase):
    def test_spills_exact_work_mem_runs(self):
        rows = [{"id": i, "v": (i * 37) % 1000} for i in range(100)]
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", "v", "--work-mem", "16", "--fan-in", "4"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stats["rows"], 100)
        self.assertEqual(stats["runs"], math.ceil(100 / 16))
        self.assertEqual(leftovers, [])
        self.assertEqual(ids(out), ids(reference_sort(rows, "v")))

    def test_merge_passes_are_minimal(self):
        # 100 runs, fan-in 4: 100 -> 25 -> 7 -> 2 intermediate runs, then the
        # final merge to the output.  That is 3 reduction passes + 1 = 4.
        rows = [{"id": i, "v": i} for i in range(1000)]
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", "v", "--work-mem", "10", "--fan-in", "4"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stats["runs"], 100)
        self.assertEqual(stats["merge_passes"], 4)
        self.assertEqual(leftovers, [])
        self.assertEqual(ids(out), list(range(1000)))

    def test_two_pass_merge(self):
        # 9 runs, fan-in 2 -> 9 -> 5 -> 3 -> 2 -> 1: four passes.
        rows = [{"id": i, "v": -i} for i in range(90)]
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", "v", "--work-mem", "10", "--fan-in", "2"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stats["runs"], 9)
        self.assertEqual(stats["merge_passes"], 4)
        self.assertEqual(leftovers, [])
        self.assertEqual(ids(out), list(range(89, -1, -1)))

    def test_runs_shorter_than_fan_in_need_one_pass(self):
        rows = [{"id": i, "v": i} for i in range(35)]
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", "v", "--work-mem", "10", "--fan-in", "8"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stats["runs"], 4)
        self.assertEqual(stats["merge_passes"], 1)
        self.assertEqual(leftovers, [])

    def test_no_spill_when_input_fits(self):
        rows = [{"id": i, "v": i} for i in range(10)]
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", "v", "--work-mem", "10", "--fan-in", "2"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stats, {"rows": 10, "runs": 0, "merge_passes": 0})
        self.assertEqual(leftovers, [])

    def test_input_far_over_quota_matches_reference(self):
        rng = random.Random(7)
        rows = [
            {
                "id": i,
                "a": rng.choice([None, 0, 1, 2, 3]),
                "b": rng.choice([None, "x", "X", "y", "Z", ""]),
                "c": rng.random(),
            }
            for i in range(20000)
        ]
        order = "a ASC, b DESC NULLS FIRST, c ASC"
        for work_mem, fan_in in ((50, 8), (97, 3), (1024, 2)):
            with self.subTest(work_mem=work_mem, fan_in=fan_in):
                code, out, stats, leftovers = run_sort_full(
                    rows, "--order", order, "--work-mem", str(work_mem), "--fan-in", str(fan_in)
                )
                self.assertEqual(code, 0)
                self.assertEqual(stats["rows"], len(rows))
                self.assertEqual(stats["runs"], math.ceil(len(rows) / work_mem))
                self.assertEqual(leftovers, [])
                self.assertEqual(ids(out), ids(reference_sort(rows, order)))

    def test_all_keys_equal_keeps_input_order(self):
        rows = [{"id": i, "v": 5, "s": "same"} for i in range(500)]
        for limit in (None, "100", "999"):
            with self.subTest(limit=limit):
                args = ["--order", "v, s", "--work-mem", "13", "--fan-in", "3"]
                if limit:
                    args += ["--limit", limit]
                code, out, stats, leftovers = run_sort_full(rows, *args)
                self.assertEqual(code, 0)
                self.assertEqual(leftovers, [])
                expect = rows if limit is None else rows[: int(limit)]
                self.assertEqual(ids(out), ids(expect))

    def test_lots_of_nulls(self):
        rows = [{"id": i, "v": None if i % 3 else i} for i in range(3000)]
        for order in ("v", "v DESC", "v NULLS FIRST", "v DESC NULLS LAST"):
            with self.subTest(order=order):
                code, out, stats, leftovers = run_sort_full(
                    rows, "--order", order, "--work-mem", "64", "--fan-in", "5"
                )
                self.assertEqual(code, 0)
                self.assertEqual(leftovers, [])
                self.assertEqual(ids(out), ids(reference_sort(rows, order)))

    def test_multi_column_mixed_with_spill(self):
        rng = random.Random(11)
        rows = [
            {
                "id": i,
                "g": rng.choice([None, "alpha", "Alpha", "beta"]),
                "n": rng.choice([None, 1, 2, 3]),
                "s": rng.choice([None, "a", "B", "b", "c"]),
            }
            for i in range(4000)
        ]
        order = "g ASC COLLATE nocase, n DESC NULLS LAST, s ASC"
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", order, "--work-mem", "70", "--fan-in", "6"
        )
        self.assertEqual(code, 0)
        self.assertEqual(leftovers, [])
        self.assertEqual(ids(out), ids(reference_sort(rows, order)))

    def test_limit_beyond_quota_uses_external_sort(self):
        rows = [{"id": i, "v": (i * 13) % 97} for i in range(1000)]
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", "v", "--limit", "300", "--work-mem", "50", "--fan-in", "4"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stats["runs"], 20)
        self.assertEqual(len(out), 300)
        self.assertEqual(leftovers, [])
        self.assertEqual(ids(out), ids(reference_sort(rows, "v", 300)))

    def test_limit_results_do_not_depend_on_quota(self):
        rows = [{"id": i, "v": i % 7} for i in range(2000)]
        results = []
        for work_mem in ("10", "500", "2000", "5000"):
            code, out, stats, leftovers = run_sort_full(
                rows, "--order", "v", "--limit", "500", "--work-mem", work_mem, "--fan-in", "4"
            )
            self.assertEqual(code, 0)
            self.assertEqual(leftovers, [])
            results.append(ids(out))
        for other in results[1:]:
            self.assertEqual(results[0], other)


class LimitHeapTests(unittest.TestCase):
    def test_limit_within_quota_never_spills(self):
        rows = [{"id": i, "v": (i * 17) % 400} for i in range(5000)]
        for work_mem, limit in (("100", "100"), ("500", "10"), ("5000", "1")):
            with self.subTest(work_mem=work_mem, limit=limit):
                code, out, stats, leftovers = run_sort_full(
                    rows, "--order", "v", "--work-mem", work_mem, "--limit", limit,
                    "--fan-in", "2",
                )
                self.assertEqual(code, 0)
                self.assertEqual(stats["runs"], 0)
                self.assertEqual(stats["merge_passes"], 0)
                self.assertEqual(stats["rows"], len(rows))
                self.assertEqual(leftovers, [])
                self.assertEqual(ids(out), ids(reference_sort(rows, "v", int(limit))))

    def test_limit_heap_stable_on_equal_keys(self):
        rows = [{"id": i, "v": i // 10} for i in range(100)]
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", "v", "--limit", "25", "--work-mem", "1000"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stats["runs"], 0)
        self.assertEqual(leftovers, [])
        self.assertEqual(ids(out), list(range(25)))

    def test_limit_zero_and_exact(self):
        rows = [{"id": i, "v": i} for i in range(50)]
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", "v", "--limit", "0", "--work-mem", "10"
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, [])
        self.assertEqual(leftovers, [])

        code, out, stats, leftovers = run_sort_full(
            rows, "--order", "v", "--limit", "50", "--work-mem", "10", "--fan-in", "4"
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(out), 50)
        self.assertEqual(leftovers, [])


class AcceptanceTests(unittest.TestCase):
    """End-to-end checks on the bundled example plus large generated inputs."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(ROOT, "examples", "orders.jsonl")
        with open(path) as fh:
            cls.example = [json.loads(line) for line in fh if line.strip()]

    def test_example_multi_column_mixed(self):
        orders = [
            "region ASC, amount DESC NULLS FIRST, customer COLLATE nocase ASC",
            "region DESC NULLS LAST, amount ASC, customer DESC",
            "amount DESC, region ASC, customer COLLATE nocase DESC NULLS FIRST",
            "customer COLLATE nocase ASC, region DESC, amount ASC NULLS LAST",
        ]
        self.assertGreater(len(self.example), 500)
        for order in orders:
            with self.subTest(order=order):
                code, out, stats, leftovers = run_sort_full(
                    self.example, "--order", order, "--work-mem", "97", "--fan-in", "3"
                )
                self.assertEqual(code, 0)
                self.assertEqual(leftovers, [])
                self.assertEqual(
                    stats["runs"], math.ceil(len(self.example) / 97)
                )
                self.assertEqual(ids(out), ids(reference_sort(self.example, order)))

    def test_input_many_times_the_quota(self):
        rng = random.Random(3)
        rows = [
            {
                "id": i,
                "a": rng.choice([None, 0, 1, 1, 2, 2, 2]),
                "b": rng.choice([None, "aa", "AA", "bb", "cc", "dd"]),
                "c": rng.choice([None, rng.random(), rng.random()]),
                "d": rng.choice([None, "x", "X", "y"]),
            }
            for i in range(30000)
        ]
        order = "a ASC NULLS FIRST, b COLLATE nocase DESC, c ASC, d DESC NULLS LAST"
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", order, "--work-mem", "2000", "--fan-in", "6"
        )
        self.assertEqual(code, 0)
        self.assertEqual(leftovers, [])
        self.assertEqual(stats["rows"], len(rows))
        self.assertEqual(stats["runs"], 15)  # 15x the quota
        self.assertEqual(stats["merge_passes"], 2)  # 15 -> 3 -> final
        self.assertEqual(ids(out), ids(reference_sort(rows, order)))

    def test_all_identical_keys_with_every_limit_shape(self):
        rows = [{"id": i, "k": "same", "n": None} for i in range(20000)]
        for limit in (None, 10000, 12345):
            with self.subTest(limit=limit):
                args = ["--order", "k, n", "--work-mem", "4000", "--fan-in", "4"]
                if limit is not None:
                    args += ["--limit", str(limit)]
                code, out, stats, leftovers = run_sort_full(rows, *args)
                self.assertEqual(code, 0)
                self.assertEqual(leftovers, [])
                expect = rows if limit is None else rows[:limit]
                self.assertEqual(ids(out), ids(expect))

    def test_limit_over_many_times_the_quota_does_not_spill(self):
        rng = random.Random(5)
        rows = [{"id": i, "a": rng.choice([None, 1, 2, 3]), "b": rng.random()} for i in range(30000)]
        order = "a DESC, b COLLATE binary ASC"
        code, out, stats, leftovers = run_sort_full(
            rows, "--order", order, "--limit", "500", "--work-mem", "500", "--fan-in", "2"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stats["rows"], len(rows))
        self.assertEqual(stats["runs"], 0)
        self.assertEqual(stats["merge_passes"], 0)
        self.assertEqual(leftovers, [])
        self.assertEqual(ids(out), ids(reference_sort(rows, order, 500)))


class QuotaTests(unittest.TestCase):
    """Whatever the path, the operator must stay inside ``work_mem`` rows."""

    def observe(self, order, work_mem, fan_in, rows, limit=None):
        from extsort import SortOperator

        op = SortOperator(
            keys.parse(order), work_mem=work_mem, fan_in=fan_in, limit=limit
        )
        peak = [0]
        original = op.hold

        def hold(n=1):
            original(n)
            peak[0] = max(peak[0], op.held)

        op.hold = hold
        with tempfile.TemporaryDirectory() as d:
            op.tmpdir = d
            out = list(op.execute(iter(rows)))
            self.assertEqual(os.listdir(d), [])
        return out, peak[0], op.stats

    def test_external_sort_peak(self):
        rows = [{"id": i, "v": (i * 31) % 211} for i in range(5000)]
        out, peak, stats = self.observe("v", 40, 8, rows)
        self.assertLessEqual(peak, 40)
        self.assertEqual(stats["runs"], math.ceil(len(rows) / 40))
        self.assertEqual(ids(out), ids(reference_sort(rows, "v")))

    def test_top_n_peak(self):
        rows = [{"id": i, "v": (i * 31) % 211} for i in range(5000)]
        out, peak, stats = self.observe("v", 40, 2, rows, limit=40)
        self.assertLessEqual(peak, 40)
        self.assertEqual(stats["runs"], 0)
        self.assertEqual(ids(out), ids(reference_sort(rows, "v", 40)))

    def test_tiny_quota(self):
        rows = [{"id": i, "v": -i} for i in range(25)]
        out, peak, stats = self.observe("v", 2, 2, rows)
        self.assertLessEqual(peak, 2)
        self.assertEqual(stats["runs"], 13)
        self.assertEqual(ids(out), ids(reference_sort(rows, "v")))


class FailureTests(unittest.TestCase):
    def test_bad_json_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "in.jsonl"), os.path.join(d, "out.jsonl")
            work = os.path.join(d, "scratch")
            os.mkdir(work)
            with open(src, "w") as fh:
                for i in range(50):
                    fh.write(json.dumps({"v": i}) + "\n")
                fh.write("{not json\n")
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "sort.py"), src, dst,
                 "--order", "v", "--work-mem", "4", "--fan-in", "2", "--tmpdir", work],
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(os.listdir(work), [])

    def test_bad_order_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.jsonl")
            work = os.path.join(d, "scratch")
            os.mkdir(work)
            with open(src, "w") as fh:
                fh.write('{"v": 1}\n')
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "sort.py"), src,
                 os.path.join(d, "out.jsonl"), "--order", "v sideways",
                 "--tmpdir", work],
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(os.listdir(work), [])

    def test_fan_in_out_of_range(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.jsonl")
            with open(src, "w") as fh:
                fh.write('{"v": 1}\n')
            for fan_in in ("1", "11"):
                proc = subprocess.run(
                    [sys.executable, os.path.join(ROOT, "sort.py"), src,
                     os.path.join(d, "out.jsonl"), "--order", "v",
                     "--work-mem", "10", "--fan-in", fan_in],
                )
                self.assertEqual(proc.returncode, 2)

    def test_cleanup_on_crash_inside_merge(self):
        """A crash during the merge pass must still remove every temp file."""
        from extsort import SortOperator

        with tempfile.TemporaryDirectory() as d:
            rows = [{"id": i, "v": i} for i in range(100)]
            op = SortOperator(keys.parse("v"), work_mem=10, fan_in=3, tmpdir=d)
            seen = []

            def boom(group):
                # Runs have been spilled by now; record them, then crash.
                seen.extend(group)
                self.assertTrue(group)
                raise RuntimeError("simulated crash")

            op._merge_to_run = boom
            with self.assertRaises(RuntimeError):
                for _ in op.execute(iter(rows)):
                    pass
            self.assertTrue(seen)
            self.assertEqual(os.listdir(d), [])

    def test_cleanup_when_consumer_stops_early(self):
        """Closing the generator mid-stream removes the run files."""
        from extsort import SortOperator

        with tempfile.TemporaryDirectory() as d:
            rows = [{"id": i, "v": i} for i in range(100)]
            op = SortOperator(keys.parse("v"), work_mem=10, fan_in=3, tmpdir=d)
            gen = op.execute(iter(rows))
            next(gen)
            gen.close()
            self.assertEqual(os.listdir(d), [])

    def test_sigterm_during_sort_cleans_temp_files(self):
        """Killing the process mid-sort must not leave run files behind."""
        import signal
        import time

        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.jsonl")
            work = os.path.join(d, "scratch")
            os.mkdir(work)
            with open(src, "w") as fh:
                for i in range(200000):
                    fh.write(json.dumps({"id": i, "v": -i}) + "\n")
            proc = subprocess.Popen(
                [
                    sys.executable,
                    os.path.join(ROOT, "sort.py"),
                    src,
                    os.path.join(d, "out.jsonl"),
                    "--order",
                    "v",
                    "--work-mem",
                    "50",
                    "--fan-in",
                    "4",
                    "--tmpdir",
                    work,
                ]
            )
            deadline = time.time() + 20
            while time.time() < deadline and not os.listdir(work):
                time.sleep(0.01)
            self.assertTrue(os.listdir(work), "sort finished before it could be killed")
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(os.listdir(work), [])


if __name__ == "__main__":
    unittest.main()
