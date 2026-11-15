#!/usr/bin/env python3
"""Reference-adjudication tests for canon.py.

Each scenario states the expected clusters and the expected arbitration
reasons by hand (derived from the rules in README.md), so a regression in
either the clustering or in the recorded reason fails loudly.

Run with::

    python3 -m unittest -v test_canon
"""

import hashlib
import json
import os
import random
import shutil
import tempfile
import unittest

import canon
from scenarios import CASES, LANGUAGES, A, B, C, D, page


def run(pages, langs=None):
    return canon.cluster({"pages": pages, "langs": langs or [], "seed": 1})


def summary(result):
    # The representative is always members[0]; assert that too.
    for cluster in result["clusters"]:
        assert cluster["rep"] == cluster["members"][0]
        assert cluster["members"] == sorted(cluster["members"])
    return [cluster["members"] for cluster in result["clusters"]]


def conflicts(result):
    out = {}
    for url, verdict in result["verdicts"].items():
        reasons = sorted(
            part for part in verdict.split(";") if part.startswith("conflict:")
        )
        if reasons:
            out[url] = reasons
    return out


def cycle_marks(result):
    return {
        url: verdict
        for url, verdict in result["verdicts"].items()
        if "cycle-min:" in verdict
    }


class ClusterCases(unittest.TestCase):
    def test_all_cases(self):
        self.assertEqual(len(CASES), 34)
        for name, pages, clusters, expected_conflicts in CASES:
            with self.subTest(name=name):
                result = run(pages, ["en", "ja"])
                self.assertEqual(summary(result), clusters)
                self.assertEqual(conflicts(result), expected_conflicts)

    def test_cycle_reason_recorded(self):
        result = run([page(A, B), page(B, A), page(D, B)])
        marks = cycle_marks(result)
        self.assertEqual(sorted(marks), sorted([A, B, D]))
        for url in marks:
            self.assertIn("cycle-min:[" + ",".join(sorted([A, B])) + "]", marks[url])

    def test_digest_matches_cluster_summary(self):
        for name, pages, _clusters, _conflicts in CASES:
            with self.subTest(name=name):
                result = run(pages, ["en"])
                expected = hashlib.sha1(
                    str(result["clusters"]).encode("utf-8")
                ).hexdigest()
                self.assertEqual(result["digest"], expected)

    def test_every_conflict_has_reason(self):
        # A page with a conflict must never be silently re-clustered: the
        # verdict must spell out *why* the declaration was ignored.
        for name, pages, _clusters, _expected in CASES:
            with self.subTest(name=name):
                result = run(pages, ["en"])
                for url, verdict in result["verdicts"].items():
                    reasons = [
                        part for part in verdict.split(";")
                        if part.startswith("conflict:")
                    ]
                    for reason in reasons:
                        # A reason is "<kind>:<detail>"; a bare kind would
                        # leave the arbitration unauditable.
                        payload = reason[len("conflict:"):]
                        self.assertGreater(
                            len(payload.split(":", 1)[-1]), 0,
                            "reason %r carries no detail" % reason,
                        )

    def test_verdict_reports_cluster_and_language(self):
        pages = [page(A, None, [("en", A), ("ja", B)]), page(B, None, [("ja", B), ("en", A)])]
        result = run(pages, ["en", "ja"])
        self.assertIn("cluster:" + A, result["verdicts"][A])
        self.assertIn("lang:en", result["verdicts"][A])
        self.assertIn("lang:ja", result["verdicts"][A])
        self.assertIn("lang:ja", result["verdicts"][B])

    def test_malformed_entries_are_reported(self):
        result = run(
            [
                {"url": A, "canonical": None, "hreflang": "nope"},
                {"url": B, "canonical": None,
                 "hreflang": [{"lang": "", "url": C}, "junk"]},
            ],
            ["en"],
        )
        self.assertEqual(summary(result), [[A], [B]])
        self.assertIn(
            "conflict:hreflang-not-a-list", result["verdicts"][A]
        )
        self.assertIn(
            "conflict:hreflang-malformed-entry", result["verdicts"][B]
        )

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            canon.cluster([])
        with self.assertRaises(ValueError):
            canon.cluster({"pages": {}})
        with self.assertRaises(ValueError):
            canon.cluster({"pages": [{"url": "  "}]})
        with self.assertRaises(ValueError):
            canon.cluster({"pages": [], "langs": "en"})


class Determinism(unittest.TestCase):
    def test_shuffled_input_gives_identical_output(self):
        rng = random.Random(20261116)
        for name, pages, _clusters, _conflicts in CASES:
            with self.subTest(name=name):
                baseline = run(pages, ["en", "ja"])
                for _ in range(5):
                    shuffled = [dict(p) for p in pages]
                    rng.shuffle(shuffled)
                    for entry in shuffled:
                        entry["hreflang"] = list(entry.get("hreflang") or [])
                        rng.shuffle(entry["hreflang"])
                    self.assertEqual(run(shuffled, ["en", "ja"]), baseline)

    def test_language_configuration_does_not_change_clusters(self):
        configs = list(LANGUAGES) + [[], ["fr"], ["EN"], ["en"]]
        for name, pages, _clusters, _conflicts in CASES:
            with self.subTest(name=name):
                baseline = run(pages, configs[0])
                for langs in configs[1:]:
                    result = run(pages, langs)
                    self.assertEqual(result["clusters"], baseline["clusters"])
                    self.assertEqual(result["digest"], baseline["digest"])

    def test_repeated_runs_are_stable(self):
        for name, pages, _clusters, _conflicts in CASES:
            with self.subTest(name=name):
                self.assertEqual(run(pages, ["en"]), run(pages, ["en"]))


class Properties(unittest.TestCase):
    """Randomised invariants over generated canonical/hreflang graphs."""

    def _random_case(self, rng):
        size = rng.randint(1, 9)
        hosts = ["a.test", "b.test", "c.test"]
        urls = [
            "https://%s/p%d" % (rng.choice(hosts), index)
            for index in range(size)
        ]
        urls = sorted(set(urls))
        languages = ["en", "ja", "de", "en-US"]

        pages = []
        for url in urls:
            # canonical: unset, self, an existing page, or an external URL.
            roll = rng.random()
            if roll < 0.35:
                canonical = None
            elif roll < 0.45:
                canonical = url
            elif roll < 0.85:
                canonical = rng.choice(urls)
            else:
                canonical = "https://ext.test/x%d" % rng.randint(0, 2)

            hl = []
            for _ in range(rng.randint(0, 3)):
                target = rng.choice(urls + ["https://ext.test/q"])
                hl.append((rng.choice(languages), target))
            pages.append(page(url, canonical, hl))
        return urls, pages

    def test_invariants_hold(self):
        rng = random.Random(4242)
        for _ in range(300):
            urls, pages = self._random_case(rng)
            result = run(pages, ["en", "ja"])
            clusters = result["clusters"]

            # Every input URL appears exactly once, reps are minimal members.
            flat = [url for cluster in clusters for url in cluster["members"]]
            self.assertEqual(sorted(flat), sorted(urls))
            for cluster in clusters:
                self.assertEqual(cluster["rep"], cluster["members"][0])
                self.assertEqual(cluster["members"], sorted(cluster["members"]))

            # Canonical components never straddle clusters.
            root, _cycles = canon._resolve_canonical(
                urls,
                {
                    url: p["canonical"]
                    for url, p in zip(urls, pages)
                    if p["canonical"] and p["canonical"] != url
                },
            )
            cluster_of = {}
            for cluster in clusters:
                for url in cluster["members"]:
                    cluster_of[url] = cluster["rep"]
            for url in urls:
                target = root[url]
                if target in cluster_of:
                    self.assertEqual(cluster_of[url], cluster_of[target])

            # Every recorded conflict reason is stable and self-describing.
            for url, verdict in result["verdicts"].items():
                for part in verdict.split(";"):
                    if part.startswith("conflict:"):
                        self.assertGreater(len(part), len("conflict:"))

            # Shuffled input and alternate language config: same summary.
            shuffled = [dict(p) for p in pages]
            for entry in shuffled:
                entry["hreflang"] = list(entry["hreflang"])
            rng.shuffle(shuffled)
            for entry in shuffled:
                rng.shuffle(entry["hreflang"])
            other = run(shuffled, ["en", "ja", "de"])
            self.assertEqual(other["clusters"], clusters)
            self.assertEqual(other["digest"], result["digest"])

    def test_reciprocal_pairs_always_share_a_cluster(self):
        rng = random.Random(99)
        for _ in range(200):
            urls, pages = self._random_case(rng)
            result = run(pages, ["en"])
            cluster_of = {}
            for cluster in result["clusters"]:
                for url in cluster["members"]:
                    cluster_of[url] = cluster["rep"]
            declarations = {
                p["url"]: {(h["lang"], h["url"]) for h in p["hreflang"]}
                for p in pages
            }
            for url, entries in declarations.items():
                for lang, target in entries:
                    if target == url or target not in declarations:
                        continue
                    back = any(t == url for _l, t in declarations[target])
                    if back:
                        self.assertEqual(cluster_of[url], cluster_of[target])
                    else:
                        self.assertIn(
                            "conflict:hreflang-one-way:", result["verdicts"][url]
                        )


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="canon-test-")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _write(self, name, payload):
        path = os.path.join(self.tempdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def _read(self, name):
        with open(os.path.join(self.tempdir, name), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_cli_round_trip(self):
        source = self._write("in.json", {
            "pages": [page(A, B), page(B, A)],
            "langs": ["en", "ja"],
            "seed": 1,
        })
        target = os.path.join(self.tempdir, "out.json")
        self.assertEqual(canon.main([source, target]), 0)
        payload = self._read("out.json")
        self.assertEqual(payload["clusters"], [{"rep": A, "members": [A, B]}])
        self.assertEqual(sorted(payload["verdicts"]), [A, B])
        self.assertEqual(
            payload["digest"],
            run([page(A, B), page(B, A)], ["en", "ja"])["digest"],
        )

    def test_cli_rejects_bad_usage(self):
        self.assertEqual(canon.main([]), 2)

    def test_cli_reports_bad_input(self):
        source = self._write("bad.json", {"pages": "nope"})
        target = os.path.join(self.tempdir, "out.json")
        self.assertEqual(canon.main([source, target]), 1)

    def test_cli_missing_file(self):
        target = os.path.join(self.tempdir, "out.json")
        missing = os.path.join(self.tempdir, "nope.json")
        self.assertEqual(canon.main([missing, target]), 1)


if __name__ == "__main__":
    unittest.main()
