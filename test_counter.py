"""Acceptance tests for counter.py.

The oracle in this module is an independent implementation: it ingests the
same stream but rebuilds every result from a full sort of the surviving
samples followed by a pairwise re-interpretation, instead of reusing the
engine's incremental insert/withdraw logic.  Agreement over fixed scenarios
and randomized streams therefore validates interval kinds, deltas, hourly
rollups, revision history and crash recovery.
"""
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import counter

ROOT = Path(__file__).resolve().parent
MOD = 1 << 16


def oracle_classify(prev, cur, cfg):
    """Pairwise interpreter, written independently from counter.classify."""
    modulus = 2 ** cfg["bits"]
    dt = cur["t"] - prev["t"]
    if cur["boot"] != prev["boot"]:
        kind, delta = "reset", None
    elif dt > cfg["max_gap"]:
        kind, delta = "gap", None
    else:
        raw = cur["value"] - prev["value"]
        wrapped = raw % modulus
        ceiling = cfg["max_rate"] * dt
        # Ambiguity: window long enough for a full revolution, or the modulo
        # increment itself is physically implausible at the declared rate.
        if ceiling >= modulus or wrapped > ceiling:
            kind, delta = "unknown", None
        elif raw < 0:
            kind, delta = "wrap", wrapped
        else:
            kind, delta = "normal", wrapped
    return {"device": prev["device"], "start": prev["t"], "end": cur["t"],
            "kind": kind, "delta": delta}


class Oracle:
    """Full-sort reference, also tracking per-prefix logical versions."""

    def __init__(self, devices):
        self.devices = devices
        # (device, t) -> winning sample
        self.winners = {}
        # (device, t) -> greatest rev seen, so out-of-order duplicates remain
        # recognizable even after their original record was superseded
        self.max_rev = {}
        self.cursor = 0
        # version -> snapshot items for change computation
        self.history = {0: self._snapshot()}

    def _snapshot(self):
        out = {}
        for name, points in self._grouped().items():
            cfg = self.devices[name]
            for a, b in zip(points, points[1:]):
                iv = oracle_classify(a, b, cfg)
                out[(name, a["t"], b["t"])] = iv
        return out

    def _grouped(self):
        grouped = {}
        for s in self.winners.values():
            grouped.setdefault(s["device"], []).append(s)
        for pts in grouped.values():
            pts.sort(key=lambda s: s["t"])
        return grouped

    def push(self, sample):
        self.cursor += 1
        key = (sample["device"], sample["t"])
        old = self.winners.get(key)
        seen_rev = self.max_rev.get(key)
        if seen_rev is not None and sample["rev"] < seen_rev:
            changed = False  # stale revision, already superseded
        elif old is not None and sample["rev"] == old["rev"]:
            if old["value"] == sample["value"] and old["boot"] == sample["boot"]:
                changed = False  # exact duplicate
            else:
                raise AssertionError("oracle: same-rev conflict")
        else:
            self.winners[key] = sample
            self.max_rev[key] = sample["rev"]
            changed = True
        snap = self._snapshot() if changed else self.history[self.cursor - 1]
        self.history[self.cursor] = snap
        return changed

    def intervals(self, version=None):
        snap = self.history[self.cursor if version is None else version]
        return [snap[k] for k in sorted(snap)]

    def hourly(self, version=None):
        acc = {}
        for iv in self.intervals(version):
            if iv["kind"] in ("normal", "wrap"):
                k = (iv["device"], iv["end"] // 3600)
                acc[k] = acc.get(k, 0) + iv["delta"]
        return [{"device": d, "hour": h, "delta": acc[(d, h)]}
                for (d, h) in sorted(acc)]

    def changes(self):
        batches = []
        for v in range(1, self.cursor + 1):
            before, after = self.history[v - 1], self.history[v]
            if before == after:
                continue
            remove = [before[k] for k in sorted(before)
                      if k not in after or after[k] != before[k]]
            upsert = [after[k] for k in sorted(after)
                      if k not in before or before[k] != after[k]]
            # Version is the consumed-prefix length (logical version), even
            # when intervening samples were no-op duplicates.
            batches.append({"version": v, "remove": remove, "upsert": upsert})
        return batches


def run_engine(devices, samples, upto=None, state_path=None):
    state = counter.new_state()
    data = {"devices": devices, "samples": samples}
    target = len(samples) if upto is None else min(upto, len(samples))
    for i in range(target):
        batch = counter.ingest_sample(
            state, samples[i], devices[samples[i]["device"]]
        )
        # The logical version is the cursor value AFTER the sample is accepted.
        state["cursor"] = i + 1
        if batch is not None:
            batch["version"] = state["cursor"]
            state["changes"].append(batch)
    return state


def assert_matches_oracle(testcase, devices, samples, state=None):
    oracle = Oracle(devices)
    for s in samples:
        oracle.push(s)
    if state is None:
        state = run_engine(devices, samples)
    testcase.assertEqual(counter.build_view(state)["intervals"],
                         oracle.intervals())
    testcase.assertEqual(counter.build_view(state)["hourly"], oracle.hourly())
    testcase.assertEqual(state["changes"], oracle.changes())
    # Every historical version must reconstruct identically via the journal.
    for v in range(0, oracle.cursor + 1):
        if v == state["cursor"]:
            view = counter.build_view(state)
        else:
            batches = [b for b in state["changes"] if b["version"] <= v]
            ivs = counter.fold_intervals(batches)
            view = {"version": v,
                    "intervals": counter.sorted_intervals(ivs),
                    "hourly": counter.build_hourly(ivs),
                    "changes": batches}
        testcase.assertEqual(view["intervals"], oracle.intervals(v),
                             "interval mismatch at version %d" % v)
        testcase.assertEqual(view["hourly"], oracle.hourly(v),
                             "hourly mismatch at version %d" % v)
    return state, oracle


class ClassifyTests(unittest.TestCase):
    def s(self, t, v, boot=1, device="d"):
        return {"device": device, "t": t, "boot": boot, "rev": 1, "value": v}

    def test_16bit_wrap_same_generation(self):
        cfg = {"bits": 16, "max_gap": 60, "max_rate": 30}
        kind, delta = counter.classify(self.s(0, 65531), self.s(2, 3), cfg)
        self.assertEqual((kind, delta), ("wrap", 8))

    def test_32bit_wrap(self):
        cfg = {"bits": 32, "max_gap": 300, "max_rate": 1_000_000}
        kind, delta = counter.classify(
            self.s(0, 2 ** 32 - 100), self.s(1, 50), cfg)
        self.assertEqual(kind, "wrap")
        self.assertEqual(delta, 150)

    def test_boot_change_is_reset_even_on_rise(self):
        cfg = {"bits": 16, "max_gap": 60, "max_rate": 30}
        kind, delta = counter.classify(
            self.s(0, 10, boot=1), self.s(1, 20, boot=2), cfg)
        self.assertEqual((kind, delta), ("reset", None))

    def test_long_gap_is_unobservable(self):
        cfg = {"bits": 16, "max_gap": 60, "max_rate": 30}
        kind, delta = counter.classify(self.s(0, 0), self.s(61, 5), cfg)
        self.assertEqual((kind, delta), ("gap", None))

    def test_rate_overflow_is_unknown_not_wrap(self):
        # Window within max_gap but modulo delta exceeds rate budget.
        cfg = {"bits": 16, "max_gap": 60, "max_rate": 1}
        kind, delta = counter.classify(self.s(0, 10), self.s(2, 20), cfg)
        self.assertEqual((kind, delta), ("unknown", None))

    def test_full_revolution_window_is_unknown(self):
        # budget >= modulus means a whole wrap cycle fits: wrap vs reset
        # cannot be told apart even for a small observed rise.
        cfg = {"bits": 16, "max_gap": 10000, "max_rate": 10}
        kind, delta = counter.classify(self.s(0, 10), self.s(7000, 11), cfg)
        self.assertEqual((kind, delta), ("unknown", None))

    def test_normal_zero_increment_is_known(self):
        cfg = {"bits": 16, "max_gap": 60, "max_rate": 30}
        kind, delta = counter.classify(self.s(0, 42), self.s(3, 42), cfg)
        self.assertEqual((kind, delta), ("normal", 0))

    def test_equality_never_wraps(self):
        cfg = {"bits": 16, "max_gap": 60, "max_rate": 30}
        kind, delta = counter.classify(
            self.s(0, 0), self.s(1, 0), cfg)
        self.assertEqual(kind, "normal")
        self.assertEqual(delta, 0)


class IncrementalTests(unittest.TestCase):
    DEV = {"d": {"bits": 16, "max_gap": 60, "max_rate": 30}}

    def s(self, t, v, rev=1, boot=1, device="d"):
        return {"device": device, "t": t, "boot": boot, "rev": rev, "value": v}

    def test_late_point_withdraws_and_replaces_spanning_interval(self):
        samples = [self.s(0, 0), self.s(10, 100), self.s(5, 50)]
        state, _ = assert_matches_oracle(self, self.DEV, samples)
        # Explicit structural check on the last batch.
        last = state["changes"][-1]
        self.assertEqual(last["version"], 3)
        self.assertEqual([(iv["start"], iv["end"]) for iv in last["remove"]],
                         [(0, 10)])
        self.assertEqual(
            sorted((iv["start"], iv["end"]) for iv in last["upsert"]),
            [(0, 5), (5, 10)])
    def test_late_point_exactly_on_wrap(self):
        # 65531 -> 3 across the gap is a wrap of 8; a late midpoint splits it
        # into a wrap leg and a normal leg whose deltas still sum to 8.
        cfg = {"d": {"bits": 16, "max_gap": 60, "max_rate": 100}}
        samples = [self.s(0, 65531), self.s(4, 3), self.s(2, 65535)]
        state, _ = assert_matches_oracle(self, cfg, samples)
        ivs = counter.build_view(state)["intervals"]
        self.assertEqual([(iv["kind"], iv["delta"]) for iv in ivs],
                         [("normal", 4), ("wrap", 4)])
        self.assertEqual(sum(iv["delta"] for iv in ivs), 8)

    def test_late_point_on_hour_boundary(self):
        cfg = {"d": {"bits": 16, "max_gap": 600, "max_rate": 100}}
        samples = [self.s(3500, 0), self.s(3700, 20), self.s(3600, 10)]
        state, _ = assert_matches_oracle(self, cfg, samples)
        view = counter.build_view(state)
        # Both pieces end in hour 1 (end-based attribution); the spanning
        # interval's end 3700 was also hour 1, so the total is preserved.
        self.assertEqual(view["hourly"],
                         [{"device": "d", "hour": 1, "delta": 20}])
        self.assertEqual([iv["end"] for iv in view["intervals"]],
                         [3600, 3700])

    def test_cross_hour_interval_not_split(self):
        # An interval crossing 3600 is attributed wholly to the end hour.
        cfg = {"d": {"bits": 16, "max_gap": 600, "max_rate": 100}}
        samples = [self.s(3500, 0), self.s(3700, 20)]
        state = run_engine(cfg, samples)
        view = counter.build_view(state)
        self.assertEqual(view["hourly"],
                         [{"device": "d", "hour": 1, "delta": 20}])

    def test_unknown_interval_excluded_from_known_deltas(self):
        cfg = {"d": {"bits": 16, "max_gap": 60, "max_rate": 1}}
        samples = [self.s(0, 0), self.s(2, 100), self.s(3, 101)]
        state, _ = assert_matches_oracle(self, cfg, samples)
        view = counter.build_view(state)
        kinds = {iv["kind"] for iv in view["intervals"]}
        self.assertIn("unknown", kinds)
        # Only the known second interval contributes; unknown adds nothing.
        self.assertEqual(view["hourly"],
                         [{"device": "d", "hour": 0, "delta": 1}])
        known_sum = sum(iv["delta"] for iv in view["intervals"]
                        if iv["delta"] is not None)
        hourly_sum = sum(h["delta"] for h in view["hourly"])
        self.assertEqual(known_sum, hourly_sum)

    def test_duplicate_is_noop_and_no_empty_batch(self):
        samples = [self.s(0, 0), self.s(1, 5), self.s(1, 5)]
        state, _ = assert_matches_oracle(self, self.DEV, samples)
        self.assertEqual(state["cursor"], 3)
        self.assertTrue(all(b["version"] != 3 for b in state["changes"]))
        self.assertEqual(len(state["changes"]), 1)
        # The single change batch is published at the logical version of the
        # second consumed sample; the duplicate advances only the cursor.
        self.assertEqual(state["changes"][0]["version"], 2)

    def test_stale_revision_ignored(self):
        samples = [self.s(0, 0), self.s(1, 5, rev=2),
                   self.s(1, 99, rev=1)]
        state, _ = assert_matches_oracle(self, self.DEV, samples)
        iv = counter.build_view(state)["intervals"][0]
        self.assertEqual(iv["delta"], 5)

    def test_correction_revision_rebuilds_affected_intervals_only(self):
        samples = [self.s(0, 0), self.s(1, 10), self.s(2, 20),
                   self.s(1, 8, rev=2)]
        state, _ = assert_matches_oracle(self, self.DEV, samples)
        last = state["changes"][-1]
        self.assertEqual(last["version"], 4)
        self.assertEqual(
            sorted((iv["start"], iv["end"]) for iv in last["remove"]),
            [(0, 1), (1, 2)])
        self.assertEqual(
            sorted((iv["start"], iv["end"]) for iv in last["upsert"]),
            [(0, 1), (1, 2)])
        view = counter.build_view(state)
        self.assertEqual([iv["delta"] for iv in view["intervals"]], [8, 12])

    def test_same_rev_conflict_raises(self):
        samples = [self.s(1, 5), self.s(1, 6)]
        state = counter.new_state()
        counter.ingest_sample(state, samples[0], self.DEV["d"])
        state["cursor"] = 1
        with self.assertRaises(ValueError):
            counter.ingest_sample(state, samples[1], self.DEV["d"])

    def test_reset_then_recovery(self):
        samples = [self.s(0, 100), self.s(1, 5, boot=2), self.s(2, 9, boot=2)]
        state, _ = assert_matches_oracle(self, self.DEV, samples)
        ivs = counter.build_view(state)["intervals"]
        self.assertEqual([iv["kind"] for iv in ivs], ["reset", "normal"])
        self.assertEqual([iv["delta"] for iv in ivs], [None, 4])

    def test_multi_device_independent_sequences(self):
        devices = {
            "a": {"bits": 16, "max_gap": 60, "max_rate": 30},
            "b": {"bits": 32, "max_gap": 120, "max_rate": 1000},
        }
        samples = [
            {"device": "a", "t": 0, "boot": 1, "rev": 1, "value": 0},
            {"device": "b", "t": 0, "boot": 1, "rev": 1, "value": 2 ** 32 - 4},
            {"device": "a", "t": 2, "boot": 1, "rev": 1, "value": 6},
            {"device": "b", "t": 2, "boot": 1, "rev": 1, "value": 2},
        ]
        assert_matches_oracle(self, devices, samples)


class RandomizedFuzzTests(unittest.TestCase):
    def _stream(self, rng, n_devices, n_samples):
        devices = {}
        for d in range(n_devices):
            bits = rng.choice([16, 32])
            devices["dev%d" % d] = {
                "bits": bits,
                "max_gap": rng.choice([30, 60, 300]),
                "max_rate": rng.choice([1, 5, 30, 1000]),
            }
        samples = []
        pos = {d: 0 for d in devices}
        boot = {d: 1 for d in devices}
        t = {d: 0 for d in devices}
        used = set()          # (device, t, rev) already emitted
        max_rev = {}          # (device, t) -> greatest rev
        for i in range(n_samples):
            name = rng.choice(list(devices))
            cfg = devices[name]
            roll = rng.random()
            if roll < 0.15:  # explicit restart
                boot[name] += 1
                pos[name] = rng.randrange(0, 100)
            elif roll < 0.30:  # long outage
                t[name] += cfg["max_gap"] + rng.randrange(1, 500)
            step = rng.randrange(0, max(2, cfg["max_rate"] * 2 + 1))
            pos[name] = (pos[name] + step) % (1 << cfg["bits"])
            t[name] += rng.randrange(1, max(2, cfg["max_gap"] // 2 + 2))
            key = (name, t[name])
            rev = max_rev.get(key, 0) + 1 if key in max_rev else 1
            sample = {"device": name, "t": t[name], "boot": boot[name],
                      "rev": rev, "value": pos[name]}
            samples.append(sample)
            used.add((name, t[name], rev))
            max_rev[key] = rev
        # Shuffle arrival order while occasionally inserting late points,
        # corrections or exact duplicates; never emit an illegal same-rev
        # conflict at an existing (device, t).
        rng.shuffle(samples)
        enriched = list(samples)
        for _ in range(n_samples // 4):
            base = rng.choice(samples)
            roll = rng.random()
            if roll < 0.4:  # late point at a fresh timestamp
                cfg = devices[base["device"]]
                lt = base["t"] - rng.randrange(1, max(2, cfg["max_gap"]))
                if lt < 0 or (base["device"], lt) in max_rev:
                    continue
                sample = {"device": base["device"], "t": lt,
                          "boot": base["boot"], "rev": 1,
                          "value": rng.randrange(0, 1 << cfg["bits"])}
                enriched.append(sample)
                max_rev[(base["device"], lt)] = 1
                used.add((base["device"], lt, 1))
            elif roll < 0.7:  # higher-rev correction
                key = (base["device"], base["t"])
                rev = max_rev[key] + 1
                cfg = devices[base["device"]]
                sample = dict(base, rev=rev,
                              value=(base["value"] + 1)
                              % (1 << cfg["bits"]))
                enriched.append(sample)
                max_rev[key] = rev
                used.add((base["device"], base["t"], rev))
            else:  # exact duplicate
                enriched.append(dict(base))
        rng.shuffle(enriched)
        return devices, enriched

    def test_fuzz_matches_independent_oracle(self):
        for seed in range(40):
            rng = random.Random(seed)
            devices, samples = self._stream(rng, rng.randrange(1, 4),
                                            rng.randrange(10, 40))
            assert_matches_oracle(self, devices, samples)

    def test_fuzz_batching_invariance(self):
        rng = random.Random(99)
        devices, samples = self._stream(rng, 3, 50)
        full = run_engine(devices, samples)
        # Replay the same logical stream in arbitrary prefix batches.
        batched = counter.new_state()
        cuts = sorted({0, len(samples)} |
                      {rng.randrange(0, len(samples) + 1) for _ in range(8)})
        for lo, hi in zip(cuts, cuts[1:]):
            for i in range(lo, hi):
                batch = counter.ingest_sample(
                    batched, samples[i], devices[samples[i]["device"]])
                batched["cursor"] = i + 1
                if batch is not None:
                    batch["version"] = batched["cursor"]
                    batched["changes"].append(batch)
        self.assertEqual(counter.build_view(batched),
                         counter.build_view(full))
        self.assertEqual(batched["changes"], full["changes"])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent / ".tmp_tests"
        base.mkdir(exist_ok=True)
        self.tmp = tempfile.mkdtemp(dir=str(base))
        self.dirp = Path(self.tmp)

    def _write_source(self, payload):
        p = self.dirp / "source.json"
        p.write_text(json.dumps(payload))
        return p

    def _cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "counter.py"), *args],
            capture_output=True, text=True)

    def test_repeated_prefix_is_idempotent(self):
        source = self._write_source({
            "devices": {"d": {"bits": 16, "max_gap": 60, "max_rate": 30}},
            "samples": [
                {"device": "d", "t": 0, "boot": 1, "rev": 1, "value": 0},
                {"device": "d", "t": 1, "boot": 1, "rev": 1, "value": 5},
                {"device": "d", "t": 2, "boot": 1, "rev": 1, "value": 9},
            ]})
        db = self.dirp / "state.db"
        r1 = self._cli("apply", str(source), str(db), "--upto", "2")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        first = json.loads(r1.stdout)
        r2 = self._cli("apply", str(source), str(db), "--upto", "2")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(json.loads(r2.stdout), first)
        r3 = self._cli("apply", str(source), str(db))
        final = json.loads(r3.stdout)
        self.assertEqual(final["version"], 3)
        # Resume: full replay from an empty DB must reach the identical truth.
        db2 = self.dirp / "state2.db"
        for cut in (1, 2, 3):
            r = self._cli("apply", str(source), str(db2), "--upto", str(cut))
            self.assertEqual(r.returncode, 0, r.stderr)
        one_shot = json.loads(
            self._cli("inspect", str(db2)).stdout)
        self.assertEqual(one_shot, final)

    def test_crash_before_publish_keeps_old_snapshot(self):
        source = self._write_source({
            "devices": {"d": {"bits": 16, "max_gap": 60, "max_rate": 30}},
            "samples": [
                {"device": "d", "t": 0, "boot": 1, "rev": 1, "value": 0},
                {"device": "d", "t": 1, "boot": 1, "rev": 1, "value": 5},
                {"device": "d", "t": 2, "boot": 1, "rev": 1, "value": 9},
            ]})
        db = self.dirp / "state.db"
        ok = self._cli("apply", str(source), str(db), "--upto", "1")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        before = json.loads(self._cli("inspect", str(db)).stdout)
        crashed = self._cli("apply", str(source), str(db),
                            "--crash-before-publish")
        self.assertEqual(crashed.returncode, counter.EXIT_CRASH)
        after = json.loads(self._cli("inspect", str(db)).stdout)
        self.assertEqual(after, before)
        # No temp files leaked, and a normal re-run converges to full truth.
        self.assertEqual(list(self.dirp.glob("*.tmp")), [])
        resumed = self._cli("apply", str(source), str(db))
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        truth = json.loads(resumed.stdout)
        fresh_db = self.dirp / "fresh.db"
        fresh = json.loads(
            self._cli("apply", str(source), str(fresh_db)).stdout)
        self.assertEqual(truth, fresh)

    def test_crash_on_empty_state_leaves_no_db(self):
        source = self._write_source({
            "devices": {"d": {"bits": 16, "max_gap": 60, "max_rate": 30}},
            "samples": [
                {"device": "d", "t": 0, "boot": 1, "rev": 1, "value": 1}]})
        db = self.dirp / "state.db"
        r = self._cli("apply", str(source), str(db),
                      "--crash-before-publish")
        self.assertEqual(r.returncode, counter.EXIT_CRASH)
        self.assertFalse(db.exists())
        self.assertEqual(list(self.dirp.glob("*.tmp")), [])
        empty = json.loads(
            self._cli("inspect", str(db), "--version", "0").stdout)
        self.assertEqual(empty,
                         {"version": 0, "intervals": [], "hourly": [],
                          "changes": []})

    def test_historical_versions_via_cli(self):
        source = self._write_source({
            "devices": {"d": {"bits": 16, "max_gap": 60, "max_rate": 30}},
            "samples": [
                {"device": "d", "t": 0, "boot": 1, "rev": 1, "value": 0},
                {"device": "d", "t": 2, "boot": 1, "rev": 1, "value": 10},
                {"device": "d", "t": 1, "boot": 1, "rev": 1, "value": 5},
            ]})
        db = self.dirp / "state.db"
        self.assertEqual(
            self._cli("apply", str(source), str(db)).returncode, 0)
        v1 = json.loads(
            self._cli("inspect", str(db), "--version", "1").stdout)
        self.assertEqual(v1["version"], 1)
        self.assertEqual(v1["intervals"], [])
        self.assertEqual(v1["changes"], [])
        v2 = json.loads(
            self._cli("inspect", str(db), "--version", "2").stdout)
        self.assertEqual(v2["version"], 2)
        self.assertEqual([(iv["start"], iv["end"], iv["delta"])
                          for iv in v2["intervals"]], [(0, 2, 10)])
        v3 = json.loads(
            self._cli("inspect", str(db), "--version", "3").stdout)
        self.assertEqual([(iv["start"], iv["end"], iv["delta"])
                          for iv in v3["intervals"]],
                         [(0, 1, 5), (1, 2, 5)])
        bad = self._cli("inspect", str(db), "--version", "99")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("not available", bad.stderr)

    def test_conflict_via_cli_fails_without_partial_publish(self):
        source = self._write_source({
            "devices": {"d": {"bits": 16, "max_gap": 60, "max_rate": 30}},
            "samples": [
                {"device": "d", "t": 1, "boot": 1, "rev": 1, "value": 5},
                {"device": "d", "t": 1, "boot": 1, "rev": 1, "value": 6},
            ]})
        db = self.dirp / "state.db"
        r = self._cli("apply", str(source), str(db))
        self.assertEqual(r.returncode, 1)
        self.assertIn("conflicting sample", r.stderr)
        self.assertFalse(db.exists())

    def test_demo_file_end_to_end(self):
        db = self.dirp / "demo.db"
        r = self._cli("apply", str(ROOT / "demo.json"), str(db))
        self.assertEqual(r.returncode, 0, r.stderr)
        view = json.loads(r.stdout)
        self.assertEqual(
            [(iv["start"], iv["end"], iv["kind"], iv["delta"])
             for iv in view["intervals"]],
            [(3599, 3600, "wrap", 5), (3600, 3601, "normal", 3)])
        self.assertEqual(view["hourly"],
                         [{"device": "x", "hour": 1, "delta": 8}])
        self.assertEqual(view["version"], 3)
        self.assertEqual(len(view["changes"]), 2)

    def test_late_point_on_wrap_and_hour_boundary_with_crash_replay(self):
        # The late sample sits exactly on t=3600 (hour boundary) and is the
        # modulo wrap point: 65531 ->(3600)-> 4 ->(3602)-> 8, i.e. a wrap of
        # 9 then a normal leg of 4.  Publish interruption must not expose a
        # partial result; replay converges to the full-sort truth.
        payload = {
            "devices": {"d": {"bits": 16, "max_gap": 600, "max_rate": 100}},
            "samples": [
                {"device": "d", "t": 3598, "boot": 1, "rev": 1,
                 "value": 65531},
                {"device": "d", "t": 3602, "boot": 1, "rev": 1, "value": 8},
                {"device": "d", "t": 3600, "boot": 1, "rev": 1, "value": 4},
            ]}
        source = self._write_source(payload)
        db = self.dirp / "state.db"
        pre = self._cli("apply", str(source), str(db), "--upto", "2")
        self.assertEqual(pre.returncode, 0, pre.stderr)
        pre_view = json.loads(pre.stdout)
        self.assertEqual(
            [(iv["kind"], iv["delta"]) for iv in pre_view["intervals"]],
            [("wrap", 13)])
        crashed = self._cli("apply", str(source), str(db),
                            "--crash-before-publish")
        self.assertEqual(crashed.returncode, counter.EXIT_CRASH)
        mid = json.loads(self._cli("inspect", str(db)).stdout)
        self.assertEqual(mid, pre_view)
        resumed = json.loads(
            self._cli("apply", str(source), str(db)).stdout)
        fresh = json.loads(
            self._cli("apply", str(source),
                      str(self.dirp / "fresh.db")).stdout)
        self.assertEqual(resumed, fresh)
        self.assertEqual(
            [(iv["start"], iv["end"], iv["kind"], iv["delta"])
             for iv in resumed["intervals"]],
            [(3598, 3600, "wrap", 9), (3600, 3602, "normal", 4)])
        # End-based attribution: both intervals end in hour 1; the wrap leg
        # ending exactly on the boundary belongs to hour 1, never hour 0.
        self.assertEqual(resumed["hourly"],
                         [{"device": "d", "hour": 1, "delta": 13}])
        # The withdrawal batch records the old spanning interval explicitly.
        last = resumed["changes"][-1]
        self.assertEqual(
            [(iv["start"], iv["end"]) for iv in last["remove"]],
            [(3598, 3602)])
        self.assertEqual(
            sorted((iv["start"], iv["end"]) for iv in last["upsert"]),
            [(3598, 3600), (3600, 3602)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
