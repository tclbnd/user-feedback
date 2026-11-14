"""Acceptance tests for the versioned event replay (stdlib unittest only).

Run with::

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import replay  # noqa: E402
from replay import (  # noqa: E402
    MissingUpcasterPath,
    ReplayEngine,
    ReplayResult,
    UpcasterRegistry,
    fold_events,
    fold_shards,
    merge_results,
    split_events,
)

FIXTURES = os.path.join(HERE, "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def sample_events():
    return load_fixture("events.json")["events"]


def reference_fold(events):
    """Small, independent reference implementation of the documented rules.

    It is deliberately written in the most direct way possible (no upcaster
    registry, no engine) so that it can catch mistakes in the production
    pipeline rather than mirroring them.
    """
    state = {"balance_cents": 0, "name": "", "applied": 0}
    rejects = []
    applied_by_version = {}
    for event in events:
        kind = event.get("type")
        version = event.get("v")
        data = event.get("data") or {}
        cents = None
        if kind in ("credit", "debit"):
            try:
                if version == 1:
                    cents = int(data["amount"])
                elif version == 2:
                    if data.get("currency") != "CNY":
                        rejects.append({"seq": event["seq"], "reason": "unsupported-currency"})
                        continue
                    cents = int(data["cents"])
                elif version == 3:
                    cents = int(round(float(data["amount"]) * 100))
                else:
                    rejects.append(
                        {"seq": event["seq"], "reason": f"no-upcast-path:v{version}->v3"}
                    )
                    continue
            except (KeyError, TypeError, ValueError):
                rejects.append({"seq": event["seq"], "reason": "invalid-amount"})
                continue
            state["balance_cents"] += cents if kind == "credit" else -cents
        elif kind == "rename":
            name = data.get("name")
            if not isinstance(name, str):
                rejects.append({"seq": event["seq"], "reason": "invalid-name"})
                continue
            state["name"] = name
        else:
            rejects.append({"seq": event["seq"], "reason": "unknown-type"})
            continue
        state["applied"] += 1
        key = str(version)
        applied_by_version[key] = applied_by_version.get(key, 0) + 1
    return {"state": state, "rejects": rejects, "applied_by_version": applied_by_version}


def generated_stream():
    """A longer, mixed-generation stream used for property style checks."""
    events = []
    kinds = ["credit", "debit", "rename", "bogus"]
    for seq in range(40):
        kind = kinds[seq % len(kinds)]
        version = (seq % 5) + 1
        if kind == "rename":
            data = {"name": f"n{seq}"}
        elif version == 1:
            data = {"amount": 100 + seq}
        elif version == 2:
            data = {"cents": 50 + seq, "currency": "CNY" if seq % 3 else "USD"}
        elif version == 3:
            data = {"amount": f"{(100 + seq) / 100:.2f}"}
        else:
            data = {"amount": 7}
        events.append({"seq": seq, "type": kind, "v": version, "data": data})
    return events


class ReferenceEquivalenceTest(unittest.TestCase):
    """Acceptance 1: three event generations must match the reference fold."""

    def test_full_fold_matches_reference(self):
        result = fold_events(sample_events()).to_output()
        self.assertEqual(result, load_fixture("expected_full.json"))

    def test_state_matches_independent_reference_implementation(self):
        events = sample_events()
        ours = fold_events(events).to_output()
        theirs = reference_fold(events)
        self.assertEqual(ours["state"], theirs["state"])
        self.assertEqual(ours["rejects"], theirs["rejects"])
        self.assertEqual(ours["applied_by_version"], theirs["applied_by_version"])

    def test_generated_streams_match_reference(self):
        events = generated_stream()
        ours = fold_events(events).to_output()
        theirs = reference_fold(events)
        self.assertEqual(ours["state"], theirs["state"])
        self.assertEqual(ours["rejects"], theirs["rejects"])
        self.assertEqual(ours["applied_by_version"], theirs["applied_by_version"])

    def test_state_fields_match_individually(self):
        result = fold_events(sample_events())
        expected = load_fixture("expected_full.json")["state"]
        self.assertEqual(result.state, expected)
        for key in ("balance_cents", "name", "applied"):
            self.assertEqual(result.state[key], expected[key])

    def test_each_generation_contributes(self):
        result = fold_events(sample_events())
        self.assertEqual(result.applied_by_version, {"1": 2, "2": 2, "3": 3, "4": 1})

    def test_v1_cents_are_not_reinterpreted_as_yuan(self):
        result = fold_events([{"seq": 0, "type": "credit", "v": 1, "data": {"amount": 1000}}])
        self.assertEqual(result.state["balance_cents"], 1000)

    def test_v3_yuan_is_converted_to_cents(self):
        result = fold_events([{"seq": 0, "type": "credit", "v": 3, "data": {"amount": "1.50"}}])
        self.assertEqual(result.state["balance_cents"], 150)

    def test_v3_without_currency_is_accepted(self):
        result = fold_events([{"seq": 0, "type": "debit", "v": 3, "data": {"amount": "0.05"}}])
        self.assertEqual(result.state["balance_cents"], -5)
        self.assertEqual(result.rejects, [])

    def test_cents_to_yuan_roundtrip_is_exact(self):
        for cents in (0, 1, 5, 99, 150, 1220, -7, 123456789):
            self.assertEqual(replay._yuan_to_cents(replay._cents_to_yuan(cents)), cents)

    def test_fractional_cents_are_rounded_half_up(self):
        self.assertEqual(replay._yuan_to_cents("0.005"), 1)
        self.assertEqual(replay._yuan_to_cents("0.004"), 0)


class MigrationRecordTest(unittest.TestCase):
    """Output requirement 3: per-event migration records, in order."""

    def test_migration_paths_match_reference(self):
        result = fold_events(sample_events())
        self.assertEqual(result.migrations, load_fixture("expected_full.json")["migrations"])

    def test_migration_path_chain_for_oldest_event(self):
        result = fold_events([{"seq": 0, "type": "credit", "v": 1, "data": {"amount": 100}}])
        self.assertEqual(result.migrations[0]["path"], [1, 2, 3])

    def test_rejected_events_have_no_migration_record(self):
        result = fold_events(sample_events())
        rejected = {item["seq"] for item in result.rejects}
        recorded = {item["seq"] for item in result.migrations}
        self.assertFalse(rejected & recorded)

    def test_iter_replay_records_yields_fold_order(self):
        result = fold_events(sample_events())
        seqs = [record["seq"] for record in replay.iter_replay_records(result)]
        self.assertEqual(seqs, sorted(seqs))


class MissingPathTest(unittest.TestCase):
    """Acceptance 1: a missing path is an explicit error naming the seq."""

    def test_unknown_version_is_rejected_with_chain_gap_reason(self):
        result = fold_events(sample_events())
        self.assertEqual(result.rejects[-1], {"seq": 8, "reason": "no-upcast-path:v9->v3"})

    def test_missing_path_does_not_touch_state(self):
        baseline = fold_events(sample_events()[:8])
        with_unknown = fold_events(sample_events()[:9])
        self.assertEqual(baseline.state, with_unknown.state)

    def test_registry_resolve_raises_for_version_gap(self):
        registry = UpcasterRegistry()
        registry.register("credit", 1, 2, lambda data: data)
        registry.freeze()
        with self.assertRaises(MissingUpcasterPath) as caught:
            registry.resolve("credit", 2)
        self.assertEqual(caught.exception.reason, "no-upcast-path:v2->v3")
        self.assertIn("v2", caught.exception.detail)

    def test_registry_resolve_raises_for_unknown_start_version(self):
        with self.assertRaises(MissingUpcasterPath):
            replay.DEFAULT_REGISTRY.resolve("credit", 9)

    def test_registry_resolve_raises_for_non_integer_version(self):
        with self.assertRaises(MissingUpcasterPath):
            replay.DEFAULT_REGISTRY.resolve("credit", "two")

    def test_non_cny_currency_is_rejected_not_assumed(self):
        event = {"seq": 0, "type": "credit", "v": 2, "data": {"cents": 9, "currency": "USD"}}
        result = fold_events([event])
        self.assertEqual(result.rejects, [{"seq": 0, "reason": "unsupported-currency"}])
        self.assertEqual(result.state, {"balance_cents": 0, "name": "", "applied": 0})

    def test_missing_v2_currency_is_rejected(self):
        result = fold_events([{"seq": 0, "type": "credit", "v": 2, "data": {"cents": 9}}])
        self.assertEqual(result.rejects, [{"seq": 0, "reason": "unsupported-currency"}])

    def test_unknown_type_is_rejected(self):
        result = fold_events([{"seq": 7, "type": "bogus", "v": 1, "data": {}}])
        self.assertEqual(result.rejects, [{"seq": 7, "reason": "unknown-type"}])
        self.assertEqual(result.state["applied"], 0)


class PrefixStabilityTest(unittest.TestCase):
    """Requirement 2: the same event prefix always folds to the same state."""

    def test_folding_same_prefix_twice_is_equal(self):
        events = sample_events()
        for cut in range(len(events) + 1):
            first = fold_events(events[:cut]).to_output()
            second = fold_events(events[:cut]).to_output()
            self.assertEqual(first, second, f"prefix of {cut} events is not stable")

    def test_fold_does_not_mutate_input(self):
        events = sample_events()
        before = json.dumps(events, sort_keys=True)
        fold_events(events)
        self.assertEqual(json.dumps(events, sort_keys=True), before)


class ShardEquivalenceTest(unittest.TestCase):
    """Acceptance 2: two shards folded in parallel merge to the single fold."""

    def test_two_shards_merge_to_single_fold(self):
        events = sample_events()
        left, right = split_events(events)
        merged = merge_results([fold_events(left), fold_events(right)])
        self.assertEqual(merged.to_output(), fold_events(events).to_output())

    def test_many_shards_merge_to_single_fold(self):
        events = sample_events()
        for count in (1, 2, 3, 5, 11, 12):
            self.assertEqual(
                fold_shards(replay._contiguous_chunks(events, count)).to_output(),
                fold_events(events).to_output(),
                f"{count} shard(s) diverged",
            )

    def test_shard_merge_is_order_independent(self):
        events = sample_events()
        left, right = split_events(events)
        first, second = fold_events(left), fold_events(right)
        self.assertEqual(
            merge_results([first, second]).to_output(),
            merge_results([second, first]).to_output(),
        )

    def test_last_rename_wins_across_shards(self):
        events = sample_events()
        left, right = split_events(events)
        merged = merge_results([fold_events(left), fold_events(right)])
        self.assertEqual(merged.state["name"], "bob")

    def test_rename_to_empty_string_still_wins(self):
        events = [
            {"seq": 0, "type": "rename", "v": 4, "data": {"name": "alice"}},
            {"seq": 1, "type": "credit", "v": 1, "data": {"amount": 10}},
            {"seq": 2, "type": "rename", "v": 4, "data": {"name": ""}},
        ]
        merged = merge_results([fold_events(events[:2]), fold_events(events[2:])])
        self.assertEqual(merged.to_output(), fold_events(events).to_output())
        self.assertEqual(merged.state["name"], "")

    def test_shards_match_single_fold_on_generated_stream(self):
        events = generated_stream()
        full = fold_events(events).to_output()
        for count in (2, 3, 7):
            chunks = replay._contiguous_chunks(events, count)
            self.assertEqual(fold_shards(chunks).to_output(), full, f"{count} shards diverged")


class SnapshotTest(unittest.TestCase):
    """Acceptance 2: snapshot resume equals the uninterrupted fold."""

    def test_resume_from_snapshot_equals_full_fold(self):
        events = sample_events()
        full = fold_events(events)
        first = fold_events(events[:4])
        resumed = fold_events(
            events[4:], initial=ReplayResult.from_snapshot(first.to_snapshot())
        )
        self.assertEqual(resumed.to_output(), full.to_output())

    def test_resume_at_every_boundary(self):
        events = sample_events()
        full = fold_events(events).to_output()
        for cut in range(len(events) + 1):
            first = fold_events(events[:cut])
            rest = fold_events(
                events[cut:], initial=ReplayResult.from_snapshot(first.to_snapshot())
            )
            self.assertEqual(rest.to_output(), full, f"resume at {cut} diverged")

    def test_resume_does_not_duplicate_records(self):
        events = sample_events()
        first = fold_events(events[:5])
        resumed = fold_events(
            events[5:], initial=ReplayResult.from_snapshot(first.to_snapshot())
        )
        seqs = [record["seq"] for record in resumed.migrations]
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_snapshot_only_caches_migrated_events(self):
        events = [{"seq": 0, "type": "credit", "v": 1, "data": {"amount": 1000}}]
        snapshot = fold_events(events).to_snapshot()
        self.assertEqual(snapshot["state"]["balance_cents"], 1000)
        self.assertEqual(snapshot["last_seq"], 0)
        self.assertEqual(snapshot["migrations"][0]["path"], [1, 2, 3])

    def test_snapshot_without_last_seq_folds_everything(self):
        restored = ReplayResult.from_snapshot({"state": {"balance_cents": 0}})
        self.assertIsNone(restored.last_seq)

    def test_legacy_minimal_snapshot_rebuilds_records(self):
        # An older tool wrote {"last_seq", "state"} only; resuming from it must
        # still produce the same output as a full fold.
        events = sample_events()
        full = fold_events(events).to_output()
        legacy = {
            "last_seq": 10,
            "state": {"balance_cents": 1220, "name": "bob", "applied": 8},
        }
        initial, todo = replay.resume_from_snapshot(events, legacy)
        self.assertEqual(todo, [])
        self.assertEqual(initial.to_output(), full)

    def test_legacy_snapshot_mid_stream_matches_full_fold(self):
        events = sample_events()
        full = fold_events(events).to_output()
        head = fold_events(events[:6])
        legacy = {"last_seq": 5, "state": dict(head.state)}
        initial, todo = replay.resume_from_snapshot(events, legacy)
        resumed = fold_events(todo, initial=initial)
        self.assertEqual(resumed.to_output(), full)

    def test_snapshot_resume_skips_rejected_events(self):
        events = [
            {"seq": 0, "type": "credit", "v": 1, "data": {"amount": 100}},
            {"seq": 1, "type": "bogus", "v": 1, "data": {}},
            {"seq": 2, "type": "credit", "v": 9, "data": {"amount": 1}},
            {"seq": 3, "type": "credit", "v": 1, "data": {"amount": 50}},
        ]
        full = fold_events(events).to_output()
        initial, todo = replay.resume_from_snapshot(
            events, fold_events(events[:2]).to_snapshot()
        )
        self.assertEqual([event["seq"] for event in todo], [2, 3])
        self.assertEqual(fold_events(todo, initial=initial).to_output(), full)


class ParallelIsolationTest(unittest.TestCase):
    """Requirement 3: concurrently replayed streams do not interfere."""

    def test_parallel_streams_are_independent(self):
        streams = build_independent_streams()
        expected = {name: fold_events(stream).to_output() for name, stream in streams.items()}
        observed = {}

        def worker(name, stream):
            observed[name] = fold_events(stream).to_output()

        threads = [
            threading.Thread(target=worker, args=(name, stream))
            for name, stream in streams.items()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(observed, expected)

    def test_engines_do_not_share_cursors(self):
        engine = ReplayEngine()
        first = ReplayResult()
        second = ReplayResult()
        engine.fold_event(first, {"seq": 0, "type": "credit", "v": 1, "data": {"amount": 500}})
        engine.fold_event(second, {"seq": 10, "type": "rename", "v": 4, "data": {"name": "carol"}})
        self.assertEqual(first.state["balance_cents"], 500)
        self.assertEqual(first.state["name"], "")
        self.assertEqual(second.state["balance_cents"], 0)
        self.assertEqual(second.state["name"], "carol")


def build_independent_streams():
    streams = {}
    for index, currency in enumerate(("CNY", "EUR", "USD", "CNY")):
        events = [
            {"seq": 0, "type": "credit", "v": 1, "data": {"amount": 100 * (index + 1)}},
            {"seq": 1, "type": "debit", "v": 3, "data": {"amount": "0.10"}},
            {"seq": 2, "type": "rename", "v": 4, "data": {"name": f"stream-{index}"}},
            {"seq": 3, "type": "credit", "v": 2, "data": {"cents": 7, "currency": currency}},
            {"seq": 4, "type": "credit", "v": 9, "data": {"amount": 1}},
        ]
        streams[f"stream-{index}"] = events
    return streams


class CLITest(unittest.TestCase):
    """The documented command line contract, including crash recovery."""

    def run_cli(self, *args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "replay.py"), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def read_json(self, path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_cli_full_fold_matches_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            proc = self.run_cli(os.path.join(FIXTURES, "events.json"), out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self.read_json(out), load_fixture("expected_full.json"))

    def test_cli_crash_then_resume_matches_full_fold(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            snapshot = os.path.join(tmp, "snap.json")
            events = os.path.join(FIXTURES, "events.json")

            interrupted = self.run_cli(events, out, "--snapshot", snapshot, "--fail-after", "5")
            self.assertEqual(interrupted.returncode, 3, interrupted.stderr)
            self.assertEqual(self.read_json(snapshot)["last_seq"], 4)

            resumed = self.run_cli(events, out, "--snapshot", snapshot)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(self.read_json(out), load_fixture("expected_full.json"))

            again = self.run_cli(events, out, "--snapshot", snapshot)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(self.read_json(out), load_fixture("expected_full.json"))

    def test_cli_shards_equal_full_fold(self):
        with tempfile.TemporaryDirectory() as tmp:
            flat = os.path.join(tmp, "flat.json")
            sharded = os.path.join(tmp, "sharded.json")
            events = os.path.join(FIXTURES, "events.json")
            self.assertEqual(self.run_cli(events, flat).returncode, 0)
            self.assertEqual(self.run_cli(events, sharded, "--shards", "3").returncode, 0)
            self.assertEqual(self.read_json(flat), self.read_json(sharded))

    def test_cli_snapshot_file_matches_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            snapshot = os.path.join(tmp, "snap.json")
            events = os.path.join(FIXTURES, "events.json")
            self.assertEqual(self.run_cli(events, out, "--snapshot", snapshot).returncode, 0)
            self.assertEqual(self.read_json(snapshot), load_fixture("expected_snapshot.json"))


class MalformedInputTest(unittest.TestCase):
    """Bad payloads are rejected, never crash the fold or corrupt state."""

    def test_malformed_events_are_isolated(self):
        events = [
            {"seq": 0, "type": "credit", "v": 1, "data": {"amount": "1000"}},
            {"seq": 1, "type": "credit", "v": 1, "data": {}},
            {"seq": 2, "type": "credit", "v": 1, "data": {"amount": 1.5}},
            {"seq": 3, "type": "credit", "v": 3, "data": {"amount": "abc"}},
            {"seq": 4, "type": "credit", "v": 3, "data": {"amount": 100}},
            {"seq": 5, "type": "rename", "v": 4, "data": {}},
            {"seq": 6, "type": "rename", "v": 2, "data": {"name": "x"}},
            {"seq": 7},
            "not-a-dict",
        ]
        result = fold_events(events)
        self.assertEqual(
            [(item["seq"], item["reason"]) for item in result.rejects],
            [
                (1, "invalid-amount"),
                (2, "invalid-amount"),
                (3, "invalid-amount"),
                (4, "invalid-amount"),
                (5, "invalid-name"),
                (7, "unknown-type"),
                (None, "unknown-type"),
            ],
        )
        # Only the valid v1 credit and the version-independent rename applied.
        self.assertEqual(result.state, {"balance_cents": 1000, "name": "x", "applied": 2})
        self.assertEqual(result.applied_by_version, {"1": 1, "2": 1})

    def test_malformed_stream_still_folds_deterministically(self):
        events = [
            {"seq": 0, "type": "credit", "v": 1, "data": {"amount": 10}},
            {"seq": 1, "type": "credit", "v": 2, "data": {"cents": "oops", "currency": "CNY"}},
            {"seq": 2, "type": "credit", "v": 1, "data": {"amount": 5}},
        ]
        first = fold_events(events).to_output()
        second = fold_events(events).to_output()
        self.assertEqual(first, second)
        self.assertEqual(first["state"]["balance_cents"], 15)

    def test_events_without_seq_do_not_break_snapshot_cursor(self):
        events = [
            {"type": "credit", "v": 1, "data": {"amount": 10}},
            {"seq": 4, "type": "credit", "v": 1, "data": {"amount": 5}},
        ]
        result = fold_events(events)
        self.assertEqual(result.last_seq, 4)
        self.assertEqual(result.state["balance_cents"], 15)


class RegistryTest(unittest.TestCase):
    """Requirement 1: the upcaster chain is registered and extendable."""

    def test_default_chain_versions(self):
        self.assertEqual(replay.DEFAULT_REGISTRY.canonical_version("credit"), 3)
        _, path, _ = replay.DEFAULT_REGISTRY.migrate("credit", 1, {"amount": 10})
        self.assertEqual(path, [1, 2, 3])

    def test_custom_upcaster_is_used(self):
        registry = replay.build_default_registry().copy()
        registry.set_canonical("credit", 4)
        registry.register("credit", 3, 4, lambda data: {"amount": "0", "currency": "CNY"})
        engine = ReplayEngine(registry.freeze())
        result = engine.fold_events(
            [{"seq": 0, "type": "credit", "v": 1, "data": {"amount": 300}}]
        )
        self.assertEqual(result.migrations[0]["path"], [1, 2, 3, 4])
        # The new terminal shape is folded, not the v3 shape.
        self.assertEqual(result.state["balance_cents"], 0)

    def test_registry_rejects_decreasing_versions(self):
        registry = UpcasterRegistry()
        with self.assertRaises(ValueError):
            registry.register("credit", 3, 2, lambda data: data)

    def test_frozen_registry_refuses_registration(self):
        with self.assertRaises(RuntimeError):
            replay.DEFAULT_REGISTRY.register("credit", 3, 4, lambda data: data)


if __name__ == "__main__":
    unittest.main()