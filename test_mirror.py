"""Acceptance tests for the resumable virtual-clock mirror crawler.

Run with:  python3 -m unittest -v test_mirror

Covers:
  * URL normalization (dot segments, repeated slashes, fragments, query
    whitelist, relative resolution, same-origin enforcement);
  * workers=1 vs workers=6 index equality on the 70-page / four-cycle graph,
    cross-checked against an independent graph-traversal oracle;
  * virtual-clock invariants: monotonicity, per-host spacing, Retry-After
    lower bound, zero access to blocked paths and external origins;
  * 429 injection and crash/restart queue recovery with no lost compliant URL.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

import mirror
import reference
import sitegen

ORIGIN = sitegen.ORIGIN
SPACING = sitegen.SPACING_MS


def run_cli(graph_path, state_path, workers, limit=None):
    cmd = [sys.executable, "mirror.py", graph_path, state_path,
           "--workers", str(workers)]
    if limit is not None:
        cmd += ["--limit", str(limit)]
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def load(path):
    with open(path) as f:
        return json.load(f)


class NormalizationTests(unittest.TestCase):
    def test_dot_segments_and_slashes(self):
        n = mirror.normalize_url
        self.assertEqual(n("/a/b/../c//d", ORIGIN),
                         ORIGIN + "/a/c/d")
        self.assertEqual(n("/a/./b/", ORIGIN), ORIGIN + "/a/b/")
        self.assertEqual(n("/a/../../x", ORIGIN), ORIGIN + "/x")
        self.assertEqual(n("/", ORIGIN), ORIGIN + "/")

    def test_fragment_and_query_whitelist(self):
        n = mirror.normalize_url
        self.assertEqual(n("/p?utm=x&id=2#frag", ORIGIN),
                         ORIGIN + "/p?id=2")
        self.assertEqual(n("/p?z=1&id=b&id=a", ORIGIN),
                         ORIGIN + "/p?id=a&id=b")
        self.assertEqual(n("/p?junk=1", ORIGIN), ORIGIN + "/p")

    def test_relative_resolution(self):
        n = mirror.normalize_url
        base = ORIGIN + "/dir/index"
        self.assertEqual(n("sub", ORIGIN, base=base), ORIGIN + "/dir/sub")
        self.assertEqual(n("../about", ORIGIN, base=base),
                         ORIGIN + "/about")
        self.assertEqual(n("/abs", ORIGIN, base=base), ORIGIN + "/abs")

    def test_same_origin_enforcement(self):
        for bad in ("https://other.test/x", "//other.test/y",
                    "http://example.test/x", "https://example.test:8080/x"):
            with self.assertRaises(ValueError):
                mirror.normalize_url(bad, ORIGIN)

    def test_idempotent(self):
        n = mirror.normalize_url
        u = n("/a/../b//c?z=9&id=1#h", ORIGIN)
        self.assertEqual(n(u, ORIGIN), u)

    def test_disallow_prefix(self):
        self.assertTrue(mirror.is_disallowed("/private/x", ["/private/"]))
        self.assertFalse(mirror.is_disallowed("/private-ish", ["/private/"]))


class SeventyPageGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = sitegen.build_graph()
        cls.ref = reference.reference_index(cls.graph)
        cls.tmp = tempfile.mkdtemp()
        cls.graph_path = os.path.join(cls.tmp, "graph.json")
        with open(cls.graph_path, "w") as f:
            json.dump(cls.graph, f)
        cls.states = {}
        for w in (1, 6):
            sp = os.path.join(cls.tmp, "state-w%d.json" % w)
            run_cli(cls.graph_path, sp, w)
            with open(sp) as f:
                cls.states[w] = json.load(f)

    def test_graph_shape(self):
        self.assertEqual(len(self.graph["pages"]), 70)

    def test_workers_agree(self):
        a, b = self.states[1], self.states[6]
        for key in ("resources", "aliases", "edges", "skipped"):
            self.assertEqual(a[key], b[key], "divergence on %s" % key)
        self.assertTrue(a["done"] and b["done"])

    def test_matches_independent_oracle(self):
        out = self.states[1]
        self.assertEqual([r["url"] for r in out["resources"]],
                         [r["url"] for r in self.ref["resources"]])
        self.assertEqual(
            dict((r["url"], r["sha256"]) for r in out["resources"]),
            dict((r["url"], r["sha256"]) for r in self.ref["resources"]))
        self.assertEqual(out["aliases"], self.ref["aliases"])
        self.assertEqual([tuple(e) for e in out["edges"]],
                         self.ref["edges"])
        mir_reasons = sorted(set(s["reason"] for s in out["skipped"]))
        self.assertEqual(mir_reasons, self.ref["skip_reasons"])

    def test_four_cycle_classes_detected(self):
        out = self.states[1]
        cycles = dict((s["url"], s["kind"])
                      for s in out["skipped"] if s["reason"] == "cycle")
        kinds = set(cycles.values())
        self.assertEqual(kinds, {"redirect_cycle", "canonical_cycle"})
        u = ORIGIN
        self.assertIn(u + "/loop-self", cycles)       # self redirect
        self.assertIn(u + "/ring-a", cycles)          # multi redirect ring
        self.assertIn(u + "/ring-b", cycles)
        self.assertIn(u + "/canon-a", cycles)         # canonical pair
        self.assertIn(u + "/canon-b", cycles)
        self.assertIn(u + "/selfcanon", cycles)       # self canonical
        # link ring (/p/10,/p/20,/p/30,/p/40 -> /) is NOT an alias cycle:
        # every member remains a fetched resource with edges
        res_urls = set(r["url"] for r in out["resources"])
        for p in ("/p/10", "/p/20", "/p/30", "/p/40", "/"):
            self.assertIn(u + p, res_urls)

    def test_canonical_groups_and_invalid_canonicals(self):
        groups = self.ref["canonical_groups"]
        u = ORIGIN
        self.assertEqual(groups[u + "/p/3?id=9"], [u + "/dup?id=1&id=2"])
        reasons = set(s["reason"] for s in self.states[1]["skipped"])
        for required in ("invalid_canonical", "canonical_disallowed",
                         "canonical_missing"):
            self.assertIn(required, reasons)

    def test_zero_access_to_blocked_and_external(self):
        for out in self.states.values():
            for req in out["requests"]:
                self.assertNotIn("/private/", req["url"])
                self.assertNotIn("other.test", req["url"])
                self.assertNotIn(":8080", req["url"])
            # blocked/external discoveries still recorded as skips
            reasons = set(s["reason"] for s in out["skipped"])
            self.assertIn("disallow", reasons)
            self.assertIn("external", reasons)

    def test_virtual_clock_invariants(self):
        for w, out in self.states.items():
            times = [r["t"] for r in out["requests"]]
            ints = [r["t"] for r in out["requests"]
                    if isinstance(r["t"], int)]
            self.assertEqual(len(ints), len(times), "non-integer timestamps")
            self.assertTrue(
                all(times[i + 1] > times[i]
                    for i in range(len(times) - 1)),
                "clock not strictly increasing at workers=%d" % w)
            self.assertTrue(
                all(times[i + 1] - times[i] >= SPACING
                    for i in range(len(times) - 1)),
                "spacing lower bound violated at workers=%d" % w)

    def test_retry_after_lower_bound(self):
        out = self.states[1]
        throttle = [r for r in out["requests"]
                    if r["url"].endswith("/throttle")]
        statuses = [r["status"] for r in throttle]
        self.assertEqual(statuses[0], 429)
        self.assertEqual(statuses[-1], 200)
        wait = throttle[-1]["t"] - throttle[0]["t"]
        self.assertGreaterEqual(
            wait, self.graph["pages"]["/throttle"]["retry_after_ms"])

    def test_dedup_single_request_per_url_status(self):
        out = self.states[1]
        counts = Counter((r["url"], r["status"]) for r in out["requests"])
        repeated = [k for k, v in counts.items() if v > 1]
        self.assertEqual(repeated, [], "duplicate scheduled request")

    def test_fair_fifo_frontier(self):
        # the two seeds must be requested before any of their descendants;
        # dedup guarantees every other URL is requested at most twice and
        # only the throttled page legitimately appears twice (429 then 200)
        out = self.states[1]
        urls = [r["url"] for r in out["requests"]]
        self.assertEqual(urls[0], ORIGIN + "/")
        self.assertEqual(urls[1], ORIGIN + "/hub-bad")
        counts = Counter(urls)
        self.assertEqual(counts[ORIGIN + "/throttle"], 2)
        repeated = [u for u, n in counts.items()
                    if n > 1 and u != ORIGIN + "/throttle"]
        self.assertEqual(repeated, [])

    def test_request_log_replayable_timestamps(self):
        # per-request records carry integer virtual ms and are sorted
        for out in self.states.values():
            ts = [r["t"] for r in out["requests"]]
            self.assertEqual(ts, sorted(ts))
            self.assertTrue(all(isinstance(t, int) for t in ts))


class RestartRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = sitegen.build_graph()
        cls.tmp = tempfile.mkdtemp()
        cls.graph_path = os.path.join(cls.tmp, "graph.json")
        with open(cls.graph_path, "w") as f:
            json.dump(cls.graph, f)
        full = os.path.join(cls.tmp, "full.json")
        run_cli(cls.graph_path, full, 1)
        cls.full = load(full)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def test_chunked_restarts_match_uninterrupted(self):
        sp = self._path("chunked.json")
        for lim in (3, 1, 5, 2, 10, 4, 7, 1000):
            run_cli(self.graph_path, sp, workers=3, limit=lim)
            st = load(sp)
            if st["done"]:
                break
        for key in ("resources", "aliases", "edges", "skipped", "requests"):
            self.assertEqual(st[key], self.full[key])

    def test_restart_around_429_preserves_retry_after(self):
        sp = self._path("throttle.json")
        run_cli(self.graph_path, sp, workers=1, limit=12)  # includes 429
        st = load(sp)
        self.assertTrue(st["partial"])
        host_state = next(iter(st["host_state"].values()))
        self.assertTrue(
            any("/throttle" in u for u in host_state["retry_after"]))
        # every discovered compliant URL survives the restart: it is either
        # already answered in the log or still queued (a 429'd URL appears in
        # both places because it must be retried)
        queued = set(host_state["queue"])
        done_urls = set(r["url"] for r in st["requests"])
        self.assertEqual(set(st["discovered"]), queued | done_urls)
        run_cli(self.graph_path, sp, workers=1)
        st = load(sp)
        for key in ("resources", "aliases", "edges", "skipped", "requests"):
            self.assertEqual(st[key], self.full[key])
        # Retry-After still respected on the resumed timeline
        th = [r for r in st["requests"] if r["url"].endswith("/throttle")]
        self.assertGreaterEqual(th[1]["t"] - th[0]["t"], 25)

    def test_restart_exactly_at_429_then_drain(self):
        sp = self._path("boundary.json")
        run_cli(self.graph_path, sp, workers=1, limit=11)
        run_cli(self.graph_path, sp, workers=1, limit=1)  # the 429 request
        st = load(sp)
        self.assertEqual(st["requests"][-1]["status"], 429)
        run_cli(self.graph_path, sp, workers=6)           # drain, new workers
        st = load(sp)
        self.assertTrue(st["done"])
        self.assertEqual(st["requests"], self.full["requests"])

    def test_rerunning_completed_state_refuses(self):
        sp = self._path("complete.json")
        run_cli(self.graph_path, sp, workers=1)
        proc = subprocess.run(
            [sys.executable, "mirror.py", self.graph_path, sp],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already complete", proc.stderr)

    def test_limit_one_drains_eventually(self):
        sp = self._path("one.json")
        runs = 0
        while True:
            run_cli(self.graph_path, sp, workers=2, limit=1)
            runs += 1
            st = load(sp)
            if st["done"]:
                break
            self.assertLess(runs, len(self.full["requests"]) + 1)
        self.assertEqual(st["resources"], self.full["resources"])
        self.assertEqual(st["edges"], self.full["edges"])

    def test_worker_count_can_change_between_restarts(self):
        sp = self._path("mix.json")
        for w, lim in ((1, 7), (6, 4), (2, 9), (1, 1), (6, None)):
            run_cli(self.graph_path, sp, workers=w, limit=lim)
            st = load(sp)
            if st["done"]:
                break
        self.assertTrue(st["done"])
        for key in ("resources", "aliases", "edges", "skipped"):
            self.assertEqual(st[key], self.full[key])

    def test_all_discovered_urls_accounted_for_at_each_checkpoint(self):
        sp = self._path("account.json")
        for lim in (2, 3, 2, 5):
            run_cli(self.graph_path, sp, workers=1, limit=lim)
            st = load(sp)
            if st["done"]:
                break
            hs = next(iter(st["host_state"].values()))
            requested = set(r["url"] for r in st["requests"])
            queued = set(hs["queue"])
            # nothing discovered is silently dropped (429 retry overlaps)
            self.assertEqual(set(st["discovered"]),
                             requested | queued)


class DemoGraphTests(unittest.TestCase):
    def test_demo_smoke(self):
        tmp = tempfile.mkdtemp()
        gp = os.path.join(tmp, "g.json")
        with open(gp, "w") as f:
            json.dump(load("demo.json"), f)
        sp = os.path.join(tmp, "s.json")
        run_cli(gp, sp, 1)
        out = load(sp)
        urls = [r["url"] for r in out["resources"]]
        self.assertEqual(urls, [ORIGIN + "/", ORIGIN + "/ok"])
        self.assertEqual(out["edges"],
                         [[ORIGIN + "/", ORIGIN + "/ok"]])
        self.assertEqual(
            [s["reason"] for s in out["skipped"]], ["disallow"])
        self.assertTrue(
            all(r["t"] == i * 3 for i, r in enumerate(out["requests"])))


class CrashStateTests(unittest.TestCase):
    """Adversarial restarts and malformed-graph tolerance."""

    def _write(self, tmp, name, obj):
        p = os.path.join(tmp, name)
        with open(p, "w") as f:
            json.dump(obj, f)
        return p

    def test_inflight_urls_are_requeued_on_restart(self):
        tmp = tempfile.mkdtemp()
        graph = {
            "origin": ORIGIN, "seeds": ["/"], "spacing_ms": 4,
            "disallow": [],
            "pages": {"/": {"body": "h", "links": ["/a", "/b"]},
                      "/a": {"body": "a"}, "/b": {"body": "b"}},
        }
        gp = self._write(tmp, "g.json", graph)
        # hand-craft a checkpoint that died with one request in flight
        state = {
            "partial": True, "clock": 12, "next_seq": 3,
            "discovered": {
                ORIGIN + "/": {"source": "seed", "seq": 0},
                ORIGIN + "/a": {"source": ORIGIN + "/", "seq": 1},
                ORIGIN + "/b": {"source": ORIGIN + "/", "seq": 2}},
            "host_state": {
                "example.test": {
                    "queue": [ORIGIN + "/b"], "inflight": 1,
                    "inflight_urls": [ORIGIN + "/a"],
                    "next_ready": 16, "retry_after": {}, "seq": 2}},
            "requests": [{"url": ORIGIN + "/", "t": 0, "status": 200}],
            "skipped": [],
            "read_pages": {ORIGIN + "/": "x"},
            "links_seen": {ORIGIN + "/": [ORIGIN + "/a", ORIGIN + "/b"]},
            "canonical_map": {}, "canonical_invalid": [],
            "redirect_map": {}}
        sp = self._write(tmp, "s.json", state)
        run_cli(gp, sp, 1)
        out = load(sp)
        self.assertTrue(out["done"])
        self.assertEqual(
            sorted(r["url"] for r in out["resources"]),
            sorted([ORIGIN + "/", ORIGIN + "/a", ORIGIN + "/b"]))
        fetched = [r["url"] for r in out["requests"]]
        self.assertIn(ORIGIN + "/a", fetched)  # requeued, not lost
        self.assertIn(ORIGIN + "/b", fetched)

    def test_redirect_chain_and_self_canonical(self):
        tmp = tempfile.mkdtemp()
        graph = {
            "origin": ORIGIN, "seeds": ["/"], "spacing_ms": 2,
            "disallow": ["/no/"],
            "pages": {
                "/": {"body": "h", "links": ["/r1", "/sc", "/no/x",
                                              "/nope"]},
                "/r1": {"redirect": "/r2"},
                "/r2": {"redirect": "/final"},
                "/final": {"body": "f"},
                "/sc": {"body": "sc", "canonical": "/sc"},
                "/no/x": {"body": "blocked"}},
        }
        gp = self._write(tmp, "g.json", graph)
        sp = os.path.join(tmp, "s.json")
        run_cli(gp, sp, 1)
        out = load(sp)
        self.assertEqual(out["aliases"][ORIGIN + "/r1"], ORIGIN + "/final")
        self.assertEqual(out["aliases"][ORIGIN + "/r2"], ORIGIN + "/final")
        cycle = [s for s in out["skipped"] if s["reason"] == "cycle"]
        self.assertEqual([s["url"] for s in cycle], [ORIGIN + "/sc"])
        reasons = [s["reason"] for s in out["skipped"]]
        self.assertIn("disallow", reasons)
        self.assertIn("missing", reasons)
        res = [r["url"] for r in out["resources"]]
        self.assertIn(ORIGIN + "/final", res)
        self.assertNotIn(ORIGIN + "/r1", res)
        self.assertNotIn(ORIGIN + "/r2", res)
        self.assertNotIn(ORIGIN + "/sc", res)

    def test_query_whitelist_merges_duplicate_pages(self):
        tmp = tempfile.mkdtemp()
        graph = {
            "origin": ORIGIN, "seeds": ["/"], "spacing_ms": 1,
            "disallow": [],
            "pages": {
                "/": {"body": "h",
                      "links": ["/p?id=1&utm=x", "/p?bad=2&id=1"]},
                "/p?id=1": {"body": "p"}},
        }
        gp = self._write(tmp, "g.json", graph)
        sp = os.path.join(tmp, "s.json")
        run_cli(gp, sp, 6)
        out = load(sp)
        self.assertEqual(
            [r["url"] for r in out["resources"]],
            [ORIGIN + "/", ORIGIN + "/p?id=1"])
        # both raw links normalize to one target -> one request, one edge
        self.assertEqual(
            out["edges"], [[ORIGIN + "/", ORIGIN + "/p?id=1"]])

    def test_cli_rejects_bad_flags_and_completed_state(self):
        proc = subprocess.run(
            [sys.executable, "mirror.py", "demo.json",
             os.path.join(tempfile.mkdtemp(), "x.json"), "--limit", "0"],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--limit", proc.stderr)

    def test_query_only_canonical_and_relative_canonical(self):
        tmp = tempfile.mkdtemp()
        graph = {
            "origin": ORIGIN, "seeds": ["/"], "spacing_ms": 1,
            "disallow": [],
            "pages": {
                "/": {"body": "h", "links": ["/d/dup", "/d/rel"]},
                "/d/dup": {"body": "dup", "canonical": "/target?id=5"},
                "/d/rel": {"body": "rel", "canonical": "../target?id=5"},
                "/target?id=5": {"body": "target"}},
        }
        gp = self._write(tmp, "g.json", graph)
        sp = os.path.join(tmp, "s.json")
        run_cli(gp, sp, 1)
        out = load(sp)
        term = ORIGIN + "/target?id=5"
        self.assertEqual(out["aliases"][ORIGIN + "/d/dup"], term)
        self.assertEqual(out["aliases"][ORIGIN + "/d/rel"], term)
        self.assertEqual(
            [r["url"] for r in out["resources"]],
            [ORIGIN + "/", term])


if __name__ == "__main__":
    unittest.main(verbosity=2)
