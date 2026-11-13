"""Acceptance tests for the recoverable batch collector.

Run from the repository root:  python3 -m unittest tests.test_outbox -v
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTBOX = ROOT / "outbox.py"

CRASH_POINTS = ("after-outbox", "after-sink", "after-checkpoint", "after-ack")


def digest(payload):
    raw = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ev(partition, offset, key, payload):
    return dict(partition=partition, offset=offset, key=key, payload=payload)


def bad(partition, offset):
    return dict(partition=partition, offset=offset, invalid=True)


class CliHarness:
    def __init__(self, workdir):
        self.workdir = Path(workdir)
        self.root = self.workdir / "root"

    def write(self, name, events):
        path = self.workdir / name
        path.write_text(json.dumps(dict(events=events)), encoding="utf-8")
        return path

    def run(self, *args, expect=0):
        proc = subprocess.run(
            [sys.executable, str(OUTBOX), str(self.root), *args],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, expect, proc.stderr)
        if proc.stdout.strip():
            return json.loads(proc.stdout)
        return None

    def lease(self, partition, generation, expect=0):
        return self.run("lease", partition, str(generation), expect=expect)

    def apply(self, path, generation, crash=None, watermark=None, expect=0):
        args = ["apply", str(path), "--lease", str(generation)]
        if crash:
            args += ["--crash", crash]
        if watermark is not None:
            args += ["--watermark", str(watermark)]
        return self.run(*args, expect=expect)

    def inspect(self):
        return self.run("inspect")

    def assertEqual(self, a, b, extra=""):
        assert a == b, "got %r want %r %s" % (a, b, extra)


class OutboxTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="outbox-test-")
        self.h = CliHarness(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertInvariant(self, view):
        # acks must never exceed checkpoints; both are monotonic offsets >= 0
        for p, cp in view["checkpoints"].items():
            self.assertGreaterEqual(cp, view["acks"].get(p, 0))
        # sink entries are unique by key and carry the canonical digest
        keys = [item["key"] for item in view["sink"]]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))
        # rejects carry the documented reasons and ordering
        rejects = view["rejects"]
        self.assertEqual(
            rejects,
            sorted(rejects, key=lambda r: (r["partition"], r["offset"])),
        )
        for rej in rejects:
            self.assertIn(rej["reason"], ("INVALID", "DIGEST_CONFLICT"))


class CanonicalDigestTest(OutboxTestBase):
    def test_matches_spec_canonical_json(self):
        payload = {"z": [1, {"b": "中文"}], "a": True}
        view = self.h.lease("p", 1)
        path = self.h.write("e.json", [ev("p", 1, "k", payload)])
        view = self.h.apply(path, 1)
        self.assertEqual(view["sink"], [dict(key="k", sha256=digest(payload))])


class HappyPathTest(OutboxTestBase):
    def _batch1(self):
        return [
            ev("p", 1, "a", {"x": 1}),
            bad("p", 2),
            ev("p", 3, "b", [2]),
            ev("q", 1, "c", {"y": 2}),
        ]

    def test_multi_partition_invalid_row_and_replay(self):
        self.h.lease("p", 1)
        self.h.lease("q", 1)
        path = self.h.write("b1.json", self._batch1())
        view = self.h.apply(path, 1)
        self.assertInvariant(view)
        self.assertEqual(view["checkpoints"], {"p": 3, "q": 1})
        self.assertEqual(view["acks"], {"p": 3, "q": 1})
        self.assertEqual(
            sorted(i["key"] for i in view["sink"]), ["a", "b", "c"]
        )
        self.assertEqual(
            view["rejects"], [dict(partition="p", offset=2, reason="INVALID")]
        )
        self.assertEqual(view["outbox"], [])  # acknowledged prefix is cleaned
        # idempotent replay of the whole batch: no duplicate sink commits
        view2 = self.h.apply(path, 1)
        self.assertInvariant(view2)
        self.assertEqual(view2["sink"], view["sink"])
        self.assertEqual(view2["checkpoints"], view["checkpoints"])

    def test_gap_holds_prefix_then_fills_in_order_contract(self):
        self.h.lease("p", 7)
        # offsets 1 and 3 arrive first; 2 is missing
        path_a = self.h.write(
            "a.json", [ev("p", 1, "a", 1), ev("p", 3, "c", 3)]
        )
        view = self.h.apply(path_a, 7)
        self.assertInvariant(view)
        # contiguous resolved prefix is offset 1; offset 3 stays buffered so
        # the order contract is preserved (it cannot be confirmed past the gap)
        self.assertEqual(view["checkpoints"], {"p": 1})
        self.assertEqual(view["acks"], {"p": 1})
        self.assertEqual(
            [r["offset"] for r in view["outbox"] if r["partition"] == "p"],
            [3],
        )
        # fill the gap (an invalid row still counts as audited resolution);
        # the already-persisted offset 3 is simply reconciled, not re-sent
        before_sink = {i["key"] for i in view["sink"]}
        path_b = self.h.write("b.json", [bad("p", 2), ev("p", 3, "c", 3)])
        view = self.h.apply(path_b, 7)
        self.assertInvariant(view)
        self.assertEqual(view["checkpoints"], {"p": 3})
        self.assertEqual(view["acks"], {"p": 3})
        self.assertEqual(
            view["rejects"], [dict(partition="p", offset=2, reason="INVALID")]
        )
        self.assertEqual(
            sorted(i["key"] for i in view["sink"]), ["a", "c"]
        )
        self.assertEqual(
            {i["key"] for i in view["sink"]}, before_sink | {"c"}
        )

    def test_digest_conflict_is_rejected_not_overwritten(self):
        self.h.lease("p", 1)
        path1 = self.h.write("one.json", [ev("p", 1, "k", {"v": 1})])
        view = self.h.apply(path1, 1)
        original = digest({"v": 1})
        self.assertEqual(view["sink"], [dict(key="k", sha256=original)])
        # new generation, same stable key at a later offset with a different
        # payload -> conflict recorded independently, sink never overwritten
        self.h.lease("p", 2)
        path2 = self.h.write(
            "two.json",
            [ev("p", 4, "k", {"v": 2}), bad("p", 5)],
        )
        view = self.h.apply(path2, 2)
        self.assertInvariant(view)
        self.assertEqual(view["sink"], [dict(key="k", sha256=original)])
        self.assertIn(
            dict(partition="p", offset=4, reason="DIGEST_CONFLICT"),
            view["rejects"],
        )
        self.assertIn(
            dict(partition="p", offset=5, reason="INVALID"),
            view["rejects"],
        )
        # offsets 2..3 were never delivered: the contiguous prefix cannot pass
        # offset 1, so neither the conflict nor the invalid row can be confirmed
        self.assertEqual(view["checkpoints"], {"p": 1})
        self.assertEqual(view["acks"], {"p": 1})
        self.assertEqual(
            sorted(r["offset"] for r in view["outbox"]), [4]
        )
        self.assertEqual(
            [r["status"] for r in view["outbox"]], ["CONFLICT"]
        )


class CrashMatrixTest(OutboxTestBase):
    """Acceptance 1: interrupt at every persistence/ACK boundary."""

    def _batch(self):
        return [
            ev("p", 1, "a", {"x": 1}),
            bad("p", 2),
            ev("p", 3, "b", {"x": 2}),
            ev("q", 1, "d", {"q": True}),
            ev("q", 2, "e", {"q": False}),
        ]

    def _reference_sink(self):
        rows = self._batch()
        return sorted(
            (
                dict(key=r["key"], sha256=digest(r["payload"]))
                for r in rows
                if "key" in r
            ),
            key=lambda item: item["key"],
        )

    def test_no_failure_reference(self):
        for p in ("p", "q"):
            self.h.lease(p, 1)
        path = self.h.write("ref.json", self._batch())
        view = self.h.apply(path, 1)
        self.assertInvariant(view)
        self.assertEqual(view["sink"], self._reference_sink())
        self.assertEqual(view["checkpoints"], {"p": 3, "q": 2})
        self.assertEqual(view["acks"], {"p": 3, "q": 2})

    def test_crash_at_each_boundary_converges_to_reference(self):
        for point in CRASH_POINTS:
            with self.subTest(crash=point):
                # each fault scenario needs an untouched root, otherwise the
                # previous subtest's completed run leaves no effective boundary
                self.h = CliHarness(
                    Path(self.tmp) / ("crash-" + point)
                )
                for p in ("p", "q"):
                    self.h.lease(p, 1)
                path = self.h.write("batch.json", self._batch())

                # first run: injected fault, exit code 97, no state regression
                self.h.apply(path, 1, crash=point, expect=97)
                mid = self.h.inspect()
                self.assertInvariant(mid)
                self._assert_boundary_state(point, mid)

                # recovery run completes; a third identical run is a no-op
                view = self.h.apply(path, 1)
                self.assertInvariant(view)
                self.assertEqual(view["sink"], self._reference_sink())
                self.assertEqual(view["checkpoints"], {"p": 3, "q": 2})
                self.assertEqual(view["acks"], {"p": 3, "q": 2})
                again = self.h.apply(path, 1)
                self.assertEqual(again["sink"], view["sink"])
                self.assertEqual(again["checkpoints"], view["checkpoints"])

    def _assert_boundary_state(self, point, view):
        if point == "after-outbox":
            self.assertEqual(view["sink"], [])
            self.assertEqual(view["checkpoints"], {"p": 0, "q": 0})
            self.assertEqual(view["acks"], {"p": 0, "q": 0})
            self.assertEqual(
                {r["status"] for r in view["outbox"]}, {"PENDING"}
            )
            self.assertIn(
                dict(partition="p", offset=2, reason="INVALID"),
                view["rejects"],
            )
        elif point == "after-sink":
            # first sink commit durable; local record not yet confirmed
            self.assertEqual(len(view["sink"]), 1)
            self.assertTrue(
                any(r["status"] == "PENDING" for r in view["outbox"])
            )
            self.assertEqual(view["checkpoints"], {"p": 0, "q": 0})
            self.assertEqual(view["acks"], {"p": 0, "q": 0})
        elif point == "after-checkpoint":
            self.assertEqual(len(view["sink"]), 4)
            self.assertEqual(view["checkpoints"], {"p": 3, "q": 2})
            self.assertEqual(
                view["acks"], {"p": 0, "q": 0}
            )  # source confirmation not saved
        elif point == "after-ack":
            self.assertEqual(view["checkpoints"], {"p": 3, "q": 2})
            self.assertEqual(view["acks"], {"p": 3, "q": 2})
    def test_unknown_commit_state_observed_then_reconciled(self):
        for p in ("p", "q"):
            self.h.lease(p, 1)
        path = self.h.write("batch.json", self._batch())
        # commit reaches the sink, then the process dies before local marking
        self.h.apply(path, 1, crash="after-sink", expect=97)
        first = self.h.inspect()
        sink_keys_before = {i["key"] for i in first["sink"]}
        self.assertEqual(len(sink_keys_before), 1)
        # second run crashes at the outbox boundary, after recovery reclassifies
        self.h.apply(path, 1, crash="after-outbox", expect=97)
        view = self.h.inspect()
        unknown = [r for r in view["outbox"] if r["status"] == "UNKNOWN"]
        self.assertTrue(unknown, "sent-but-unconfirmed rows must be UNKNOWN")
        # sink untouched during a run that stopped at the outbox boundary
        self.assertEqual(
            {i["key"] for i in view["sink"]}, sink_keys_before
        )
        # reconciliation must not double-commit; final sink matches reference
        view = self.h.apply(path, 1)
        self.assertEqual(
            {i["key"] for i in view["sink"]},
            {i["key"] for i in self._reference_sink()},
        )
        self.assertEqual(view["sink"], self._reference_sink())


class DualConsumerLeaseTest(OutboxTestBase):
    """Acceptance 2: lease hand-off while an old instance is paused."""

    def test_lease_generation_blocks_old_instance_buffers_prefix(self):
        h = self.h
        h.lease("p", 1)
        # consumer A (gen 1) ingests an initial batch up to offset 2
        path1 = h.write("a.json", [ev("p", 1, "a", 1), ev("p", 2, "b", 2)])
        view = h.apply(path1, 1)
        self.assertEqual(view["checkpoints"], {"p": 2})

        # consumer B takes over with a newer generation; A is paused
        h.lease("p", 2)
        # stale generation apply must exit 3 and move nothing
        stale = h.apply(path1, 1, expect=3)
        self.assertIsNone(stale)
        view = h.inspect()
        self.assertEqual(view["leases"], {"p": 2})
        self.assertEqual(view["checkpoints"], {"p": 2})

        # B receives rows 3..5 while row 4 (gap) is still missing; offset 3 is
        # resolved and extends the prefix, offset 5 stays buffered past the gap
        path2 = h.write(
            "b.json",
            [ev("p", 3, "c", 3), ev("p", 5, "e", 5)],
        )
        view = h.apply(path2, 2)
        self.assertInvariant(view)
        self.assertEqual(view["checkpoints"], {"p": 3})
        self.assertEqual(view["acks"], {"p": 3})
        self.assertEqual(
            sorted(r["offset"] for r in view["outbox"]), [5]
        )

        # old instance A resumes and replays its cached messages: still barred
        h.apply(path1, 1, expect=3)
        h.apply(
            h.write("old.json", [ev("p", 1, "a", 1)]), 1, expect=3
        )
        view = h.inspect()
        self.assertEqual(view["checkpoints"], {"p": 3})

        # gap row 4 is itself an audit failure; prefix now extends through 5
        path3 = h.write(
            "c.json",
            [bad("p", 4), ev("p", 5, "e", 5)],
        )
        view = h.apply(path3, 2)
        self.assertInvariant(view)
        self.assertEqual(view["checkpoints"], {"p": 5})
        self.assertEqual(view["acks"], {"p": 5})
        self.assertIn(
            dict(partition="p", offset=4, reason="INVALID"), view["rejects"]
        )

    def test_digest_conflict_under_lease_switch_is_independent_record(self):
        h = self.h
        h.lease("p", 1)
        h.apply(h.write("a.json", [ev("p", 1, "k", {"v": 1})]), 1)
        h.lease("p", 2)
        view = h.apply(
            h.write("b.json", [ev("p", 2, "k", {"v": 999})]), 2
        )
        self.assertEqual(
            view["sink"], [dict(key="k", sha256=digest({"v": 1}))]
        )
        self.assertEqual(
            view["rejects"],
            [dict(partition="p", offset=2, reason="DIGEST_CONFLICT")],
        )
        self.assertEqual(view["checkpoints"], {"p": 2})

    def test_lease_commands_are_monotonic_and_idempotent(self):
        self.h.lease("p", 5)
        view = self.h.lease("p", 5)  # same generation: no side effect
        self.assertEqual(view["leases"], {"p": 5})
        self.h.lease("p", 4, expect=3)  # older generation refused
        view = self.h.lease("p", 6)
        self.assertEqual(view["leases"], {"p": 6})


class WatermarkTest(OutboxTestBase):
    def test_behind_watermark_rows_are_deferred_and_replayable(self):
        h = self.h
        h.lease("p", 1)
        # watermark 2 with checkpoint 0: offsets > 2 are refused, not stored
        path = h.write(
            "w.json",
            [
                ev("p", 1, "a", 1),
                bad("p", 2),
                ev("p", 3, "c", 3),
                ev("p", 9, "z", 9),
            ],
        )
        view = h.apply(path, 1, watermark=2)
        self.assertInvariant(view)
        self.assertEqual(view["checkpoints"], {"p": 2})
        self.assertEqual(
            view["backpressure"],
            [dict(partition="p", offset=3, reason="BEHIND_WATERMARK"),
             dict(partition="p", offset=9, reason="BEHIND_WATERMARK")],
        )
        # nothing beyond the watermark leaked into the sink or outbox
        self.assertEqual([i["key"] for i in view["sink"]], ["a"])
        self.assertEqual(
            [r["offset"] for r in view["outbox"] if r["partition"] == "p"],
            [],
        )
        # once the prefix advances, a replay admits the previously deferred row
        view = h.apply(
            h.write("r.json", [ev("p", 3, "c", 3)]), 1, watermark=2
        )
        self.assertEqual(view["backpressure"], [])
        self.assertEqual(view["checkpoints"], {"p": 3})
        self.assertEqual(
            sorted(i["key"] for i in view["sink"]), ["a", "c"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
