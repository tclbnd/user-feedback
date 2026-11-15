"""Acceptance tests for crawl.py.

Run with:  python3 -B -m unittest discover -s tests -v
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRAWL = ROOT / "crawl.py"


def rule_digest(rule):
    blob = json.dumps(rule, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def row(event_id, source, partition, seq, key, schema, revision, op, data=None):
    event = {
        "id": event_id, "kind": "row", "source": source, "partition": partition,
        "seq": seq, "key": key, "schema": schema, "revision": revision, "op": op,
    }
    if data is not None:
        event["data"] = data
    return event


def approve(event_id, source, partition, schema, rule, from_seq, through_seq, digest=None):
    return {
        "id": event_id, "kind": "approve", "source": source, "partition": partition,
        "schema": schema, "rule": rule,
        "rule_sha256": rule_digest(rule) if digest is None else digest,
        "from_seq": from_seq, "through_seq": through_seq,
    }


RULE_V1 = {"amount_field": "amount", "multiplier": 100, "enum_field": "status",
           "enum": {"ready": "ready", "held": "held"}}
RULE_V2 = {"amount_field": "amount_minor", "multiplier": 1, "enum_field": "state",
           "enum": {"READY": "ready", "HELD": "held"}}


def scenario():
    """Three sources, mixed schemas, conflicts, late revisions, deletes."""
    return {
        "initial_rules": [
            {"source": "east", "schema": "v1", "rule": RULE_V1},
            {"source": "west", "schema": "w1", "rule": RULE_V1},
        ],
        "events": [
            # east: nominal v1 rows in cents conversion (amount * 100).
            row(1, "east", "d1", 1, "inv-1", "v1", 1, "upsert", {"amount": 2, "status": "ready"}),
            row(2, "east", "d1", 2, "inv-2", "v1", 1, "upsert", {"amount": 5, "status": "held"}),
            # east: unknown schema -> quarantine, still acknowledges seq 3.
            row(3, "east", "d1", 3, "inv-3", "v9", 1, "upsert", {"amount_minor": 731, "state": "READY"}),
            # east: bad amount column (string) -> invalid_amount at "amount".
            row(4, "east", "d1", 4, "inv-4", "v1", 1, "upsert", {"amount": "12", "status": "ready"}),
            # east: unknown enum label -> invalid_enum at "status".
            row(5, "east", "d1", 5, "inv-5", "v1", 1, "upsert", {"amount": 3, "status": "gone"}),
            # east gap: seq 7 never arrives, so the cursor stops at 6.
            row(6, "east", "d1", 6, "inv-6", "v1", 1, "upsert", {"amount": 1, "status": "ready"}),
            # west: partition d1, nominal rows.
            row(7, "west", "d1", 1, "w-1", "w1", 1, "upsert", {"amount": 9, "status": "ready"}),
            row(8, "west", "d1", 2, "w-2", "w1", 1, "upsert", {"amount": 4, "status": "held"}),
            # west: delete of w-2 at revision 2.
            row(9, "west", "d1", 3, "w-2", "w1", 2, "delete"),
            # west: late revision 1 for the deleted key cannot resurrect it.
            row(10, "west", "d1", 4, "w-2", "w1", 1, "upsert", {"amount": 7, "status": "ready"}),
            # west: newer revision 3 re-creates the key with a new value.
            row(11, "west", "d1", 5, "w-2", "w1", 3, "upsert", {"amount": 8, "status": "ready"}),
            # west: duplicate of the same capture row (same seq, same content).
            row(12, "west", "d1", 1, "w-1", "w1", 1, "upsert", {"amount": 9, "status": "ready"}),
            # west: same seq 2 with different content -> conflict, keep first.
            row(13, "west", "d1", 2, "w-2", "w1", 1, "upsert", {"amount": 6, "status": "ready"}),
            # west: seq 6 with an unknown schema, quarantined.
            row(14, "west", "d1", 6, "w-3", "w9", 1, "upsert", {"amount_minor": 1234, "state": "HELD"}),
            # east: approval over the earlier quarantined seqs (3..5).
            approve(15, "east", "d1", "v9", RULE_V2, 3, 3),
            approve(16, "east", "d1", "v1", RULE_V1, 4, 5),
        ],
    }


def expected_reference(sim):
    """Independent, deliberately simple reference for the scenario above."""
    return {
        "rows": [
            {"source": "east", "partition": "d1", "key": "inv-1", "revision": 1,
             "amount_cents": 200, "status": "ready"},
            {"source": "east", "partition": "d1", "key": "inv-2", "revision": 1,
             "amount_cents": 500, "status": "held"},
            {"source": "east", "partition": "d1", "key": "inv-3", "revision": 1,
             "amount_cents": 731, "status": "ready"},
            {"source": "east", "partition": "d1", "key": "inv-6", "revision": 1,
             "amount_cents": 100, "status": "ready"},
            {"source": "west", "partition": "d1", "key": "w-1", "revision": 1,
             "amount_cents": 900, "status": "ready"},
            {"source": "west", "partition": "d1", "key": "w-2", "revision": 3,
             "amount_cents": 800, "status": "ready"},
        ],
        "tombstones": [],
        "quarantine": [
            {"source": "east", "partition": "d1", "seq": 4, "schema": "v1",
             "reason": "invalid_amount", "column": "amount",
             "data": {"amount": "12", "status": "ready"}},
            {"source": "east", "partition": "d1", "seq": 5, "schema": "v1",
             "reason": "invalid_enum", "column": "status",
             "data": {"amount": 3, "status": "gone"}},
            {"source": "west", "partition": "d1", "seq": 6, "schema": "w9",
             "reason": "unknown_schema", "column": None,
             "data": {"amount_minor": 1234, "state": "HELD"}},
        ],
        "conflicts": [
            {"source": "west", "partition": "d1", "seq": 2, "id": 13,
             "reason": "sequence_conflict"},
        ],
        "cursor": {"east/d1": 6, "west/d1": 6},
        "backfilled": [
            {"id": 3, "source": "east", "partition": "d1", "seq": 3,
             "approval_id": 15, "outcome": "applied"},
        ],
    }


class CrawlCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crawl-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    def write_sim(self, sim, name="sim.json"):
        path = self.tmp / name
        path.write_text(json.dumps(sim, ensure_ascii=False), encoding="utf-8")
        return path

    def crawl(self, sim, name="out.json", state=None, fail_at=None):
        sim_path = self.write_sim(sim)
        out_path = self.tmp / name
        cmd = [sys.executable, "-B", str(CRAWL), str(sim_path), str(out_path)]
        if state is not None:
            cmd += ["--state", str(state)]
        if fail_at is not None:
            cmd += ["--fail-at", fail_at]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        report = None
        if out_path.exists():
            report = json.loads(out_path.read_text(encoding="utf-8"))
        return proc, report

    def assert_ran_clean(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # -- acceptance 1 ----------------------------------------------------
    def test_reference_values_and_isolation(self):
        sim = scenario()
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)

        expected = expected_reference(sim)
        for field in ("rows", "tombstones", "quarantine", "conflicts", "cursor", "backfilled"):
            self.assertEqual(report[field], expected[field], field)

        # Amounts are exact integer cents everywhere.
        for entry in report["rows"]:
            self.assertIsInstance(entry["amount_cents"], int)

    def test_audit_chain_has_ids_schemas_and_approvals(self):
        sim = scenario()
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)

        # One decision per input event; the only extra entries are decisions
        # triggered by an approval (backfill), which carry its approval_id.
        event_ids = {entry["id"] for entry in report["audit"]}
        self.assertEqual(event_ids, set(range(1, 17)) - {12})
        for identity in (("west", "d1", 6), ("east", "d1", 4), ("east", "d1", 5)):
            source, partition, seq = identity
            decisions = [
                entry for entry in report["audit"]
                if (entry.get("source"), entry.get("partition"), entry.get("seq")) == identity
            ]
            self.assertEqual([e["action"] for e in decisions], ["quarantined"], identity)
        backfilled_seq3 = [
            entry for entry in report["audit"]
            if (entry.get("source"), entry.get("partition"), entry.get("seq")) == ("east", "d1", 3)
        ]
        self.assertEqual([e["action"] for e in backfilled_seq3], ["quarantined", "backfilled"])

        by_identity = {}
        for entry in report["audit"]:
            identity = (entry.get("source"), entry.get("partition"), entry.get("seq"))
            by_identity.setdefault(identity, []).append(entry)

        first3 = by_identity[("east", "d1", 3)][0]
        self.assertEqual(first3["action"], "quarantined")
        self.assertEqual(first3["schema"], "v9")
        self.assertEqual(by_identity[("east", "d1", 4)][0]["action"], "quarantined")
        self.assertEqual(by_identity[("west", "d1", 2)][0]["action"], "accepted")
        self.assertEqual(by_identity[("west", "d1", 4)][0]["action"], "stale_revision")

        backfilled = [entry for entry in report["audit"] if entry["action"] == "backfilled"]
        self.assertEqual(len(backfilled), 1)
        self.assertEqual(backfilled[0]["approval_id"], 15)
        self.assertEqual(backfilled[0]["seq"], 3)
        self.assertEqual(backfilled[0]["schema"], "v9")

        # The backfill decision comes after its approval and before later ones.
        approve_index = next(i for i, e in enumerate(report["audit"]) if e["id"] == 15)
        backfill_index = next(
            i for i, e in enumerate(report["audit"]) if e["action"] == "backfilled"
        )
        next_event_index = next(i for i, e in enumerate(report["audit"]) if e["id"] == 16)
        self.assertLess(approve_index, backfill_index)
        self.assertLess(backfill_index, next_event_index)

    def test_quarantine_does_not_advance_cursor_past_gap(self):
        sim = {
            "initial_rules": [{"source": "east", "schema": "v1", "rule": RULE_V1}],
            "events": [
                row(1, "east", "d1", 1, "a", "v1", 1, "upsert", {"amount": 1, "status": "ready"}),
                row(2, "east", "d1", 3, "b", "v8", 1, "upsert", {"amount": 1}),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        # seq 3 is acknowledged (quarantined) but 2 is missing.
        self.assertEqual(report["cursor"], {"east/d1": 1})
        self.assertEqual(len(report["quarantine"]), 1)

    def test_backfill_does_not_change_healthy_partitions(self):
        sim = scenario()
        # Split the log: everything except the approvals first.
        early = {"initial_rules": sim["initial_rules"],
                 "events": [e for e in sim["events"] if e["id"] < 15]}
        late = {"initial_rules": sim["initial_rules"],
                "events": [e for e in sim["events"] if e["id"] >= 15]}

        state = self.tmp / "backfill-state.json"
        proc, before = self.crawl(early, name="before.json", state=state)
        self.assert_ran_clean(proc)
        proc, after = self.crawl(late, name="after.json", state=state)
        self.assert_ran_clean(proc)

        # Healthy rows keep identical business values across the backfill.
        def rows_of(report, source):
            return [entry for entry in report["rows"] if entry["source"] == source]

        self.assertEqual(rows_of(before, "west"), rows_of(after, "west"))
        west_tombstones = [t for t in before["tombstones"] if t["source"] == "west"]
        self.assertEqual(west_tombstones, [t for t in after["tombstones"] if t["source"] == "west"])

        east_before = {entry["key"]: entry for entry in rows_of(before, "east")}
        east_after = {entry["key"]: entry for entry in rows_of(after, "east")}
        for key in ("inv-1", "inv-2", "inv-6"):
            self.assertEqual(east_before[key], east_after[key])
        # Only the quarantined row was added by the backfill.
        self.assertEqual(set(east_after) - set(east_before), {"inv-3"})
        self.assertEqual(east_after["inv-3"]["amount_cents"], 731)
        self.assertEqual(len(before["quarantine"]), 4)
        self.assertEqual(len(after["quarantine"]), 3)
        # The quarantined row was already acknowledged, so cursors do not move.
        self.assertEqual(before["cursor"], after["cursor"])

    def test_late_old_revision_cannot_resurrect_deleted_key(self):
        sim = {
            "initial_rules": [{"source": "west", "schema": "w1", "rule": RULE_V1}],
            "events": [
                row(1, "west", "d1", 1, "w", "w1", 2, "upsert", {"amount": 5, "status": "ready"}),
                row(2, "west", "d1", 2, "w", "w1", 3, "delete"),
                row(3, "west", "d1", 3, "w", "w1", 1, "upsert", {"amount": 9, "status": "ready"}),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["tombstones"],
                         [{"source": "west", "partition": "d1", "key": "w", "revision": 3}])

    def test_revoked_approval_cannot_backfill(self):
        sim = {
            "initial_rules": [],
            "events": [
                row(1, "east", "d1", 1, "a", "v9", 1, "upsert", {"amount_minor": 10, "state": "READY"}),
                {"id": 2, "kind": "revoke", "approval_id": 3},
                approve(3, "east", "d1", "v9", RULE_V2, 1, 1),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual(report["backfilled"], [])
        self.assertEqual(len(report["quarantine"]), 1)

    def test_approval_digest_mismatch_is_rejected_only(self):
        bad = approve(2, "east", "d1", "v9", RULE_V2, 1, 1, digest="0" * 64)
        sim = {
            "initial_rules": [],
            "events": [
                row(1, "east", "d1", 1, "a", "v9", 1, "upsert", {"amount_minor": 10, "state": "READY"}),
                bad,
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        actions = {entry["id"]: entry["action"] for entry in report["audit"]}
        self.assertEqual(actions[2], "approval_rejected")
        self.assertEqual(len(report["quarantine"]), 1)

    def test_approval_scoped_to_schema_and_interval(self):
        sim = {
            "initial_rules": [],
            "events": [
                row(1, "east", "d1", 1, "a", "v9", 1, "upsert", {"amount_minor": 10, "state": "READY"}),
                row(2, "east", "d1", 2, "b", "v9", 1, "upsert", {"amount_minor": 20, "state": "READY"}),
                approve(3, "east", "d1", "v9", RULE_V2, 1, 1),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual([entry["key"] for entry in report["rows"]], ["a"])
        self.assertEqual([entry["seq"] for entry in report["quarantine"]], [2])

    def test_backfilled_old_row_cannot_override_newer_effective_value(self):
        sim = {
            "initial_rules": [{"source": "east", "schema": "v1", "rule": RULE_V1}],
            "events": [
                row(1, "east", "d1", 1, "a", "v9", 1, "upsert", {"amount_minor": 10, "state": "READY"}),
                row(2, "east", "d1", 2, "a", "v1", 4, "upsert", {"amount": 3, "status": "held"}),
                approve(3, "east", "d1", "v9", RULE_V2, 1, 1),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual(report["rows"], [{"source": "east", "partition": "d1", "key": "a",
                                           "revision": 4, "amount_cents": 300, "status": "held"}])
        self.assertEqual(report["backfilled"][0]["outcome"], "stale_revision")

    def test_chunks_delivered_in_reverse_order_match_single_batch(self):
        sim = scenario()
        proc, reference = self.crawl(sim, name="forward.json")
        self.assert_ran_clean(proc)

        state = self.tmp / "reverse-state.log"
        events = sim["events"]
        chunks = [events[:5], events[5:10], events[10:]]
        for chunk in reversed(chunks):
            batch = {"initial_rules": sim["initial_rules"], "events": chunk}
            proc, _ = self.crawl(batch, name="reverse.json", state=state)
            self.assert_ran_clean(proc)
        final = json.loads((self.tmp / "reverse.json").read_text(encoding="utf-8"))
        self.assertEqual(final, reference)

    # -- crash points and journal recovery --------------------------------
    def crash_and_resume(self, sim, fail_at, resume_events=None):
        """Run to a forced exit, then resume with the same state file."""
        tag = fail_at.replace(":", "-")
        state = self.tmp / ("state-%s.json" % tag)
        sim_path = self.write_sim(sim)

        full_out = self.tmp / ("full-%s.json" % tag)
        proc = subprocess.run(
            [sys.executable, "-B", str(CRAWL), str(sim_path), str(full_out)],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assert_ran_clean(proc)
        full_report = json.loads(full_out.read_text(encoding="utf-8"))

        crash_out = self.tmp / ("crash-%s.json" % tag)
        proc = subprocess.run(
            [sys.executable, "-B", str(CRAWL), str(sim_path), str(crash_out),
             "--state", str(state), "--fail-at", fail_at],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 75, proc.stderr)

        # Resume by replaying the whole input (a superset of what was seen).
        resume_sim = {"initial_rules": sim["initial_rules"],
                      "events": resume_events if resume_events is not None else sim["events"]}
        resume_path = self.write_sim(resume_sim, name="resume-sim-%s.json" % tag)
        resumed_out = self.tmp / ("resumed-%s.json" % tag)
        proc = subprocess.run(
            [sys.executable, "-B", str(CRAWL), str(resume_path), str(resumed_out),
             "--state", str(state)],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assert_ran_clean(proc)
        resumed = json.loads(resumed_out.read_text(encoding="utf-8"))
        return full_report, resumed

    def test_crash_after_row_resumes_to_same_result(self):
        sim = scenario()
        for fail_at in ("after-row:7", "before-commit:7", "before-ack:7"):
            full, resumed = self.crash_and_resume(sim, fail_at)
            self.assertEqual(resumed, full, fail_at)

    def test_crash_points_leave_different_durable_prefixes(self):
        sim = scenario()
        committed = {}
        for fail_at in ("after-row:7", "before-commit:7", "before-ack:7"):
            state = self.tmp / ("prefix-%s.log" % fail_at.replace(":", "-"))
            proc, _ = self.crawl(sim, name="prefix.json", state=state, fail_at=fail_at)
            self.assertEqual(proc.returncode, 75, proc.stderr)
            records = [json.loads(line) for line in
                       state.read_text(encoding="utf-8").splitlines() if line.strip()]
            committed[fail_at] = [r["event"]["id"] for r in records if r["type"] == "event"]

        # Before the commit point nothing of event 7 is durable; only the
        # before-ack hook fires after the journal append.
        self.assertEqual(committed["after-row:7"], list(range(1, 7)))
        self.assertEqual(committed["before-commit:7"], list(range(1, 7)))
        self.assertEqual(committed["before-ack:7"], list(range(1, 8)))

    def test_torn_journal_tail_is_ignored(self):
        sim = scenario()
        state = self.tmp / "torn.log"
        proc, reference = self.crawl(sim, name="torn-ref.json")
        self.assert_ran_clean(proc)

        # Complete run, then simulate a half-written final commit.
        proc, _ = self.crawl(sim, name="torn-run.json", state=state)
        self.assert_ran_clean(proc)
        text = state.read_text(encoding="utf-8")
        state.write_text(text[:-40], encoding="utf-8")     # truncate mid-record

        out = self.tmp / "torn-resumed.json"
        proc = subprocess.run(
            [sys.executable, "-B", str(CRAWL), str(self.tmp / "sim.json"), str(out),
             "--state", str(state)],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assert_ran_clean(proc)
        resumed = json.loads(out.read_text(encoding="utf-8"))
        # Only the torn event is replayed; the result matches the clean run.
        self.assertEqual(resumed, reference)

    def test_crash_on_approval_event_resumes_without_duplicate_backfill(self):
        sim = scenario()
        for fail_at in ("after-row:15", "before-commit:15", "before-ack:15"):
            full, resumed = self.crash_and_resume(sim, fail_at)
            self.assertEqual(resumed, full, fail_at)
            self.assertEqual(len([b for b in resumed["backfilled"] if b["seq"] == 3]), 1)

    def test_shuffled_and_duplicated_input_gives_same_result(self):
        sim = scenario()
        proc, reference = self.crawl(sim, name="ordered.json")
        self.assert_ran_clean(proc)

        shuffled = {"initial_rules": sim["initial_rules"],
                    "events": list(reversed(sim["events"])) + sim["events"][:5]}
        proc, report = self.crawl(shuffled, name="shuffled.json")
        self.assert_ran_clean(proc)
        self.assertEqual(report, reference)

    def test_rechunked_delivery_matches_single_batch(self):
        sim = scenario()
        proc, reference = self.crawl(sim, name="one-shot.json")
        self.assert_ran_clean(proc)

        state = self.tmp / "chunk-state.json"
        events = sim["events"]
        chunks = [events[:4], events[4:9], events[9:13], events[13:]]
        for index, chunk in enumerate(chunks):
            batch = {"initial_rules": sim["initial_rules"], "events": chunk}
            proc, _ = self.crawl(batch, name="chunk-%d.json" % index, state=state)
            self.assert_ran_clean(proc)
        final = json.loads((self.tmp / ("chunk-%d.json" % (len(chunks) - 1)))
                           .read_text(encoding="utf-8"))
        self.assertEqual(final, reference)

    # -- extra edge cases ------------------------------------------------
    def test_number_is_never_coerced_from_string_or_null(self):
        sim = {
            "initial_rules": [{"source": "east", "schema": "v1", "rule": RULE_V1}],
            "events": [
                row(1, "east", "d1", 1, "a", "v1", 1, "upsert", {"amount": "5", "status": "ready"}),
                row(2, "east", "d1", 2, "b", "v1", 1, "upsert", {"amount": None, "status": "ready"}),
                row(3, "east", "d1", 3, "c", "v1", 1, "upsert", {"amount": 5.0, "status": "ready"}),
                row(4, "east", "d1", 4, "d", "v1", 1, "upsert", {"status": "ready"}),
                row(5, "east", "d1", 5, "e", "v1", 1, "upsert", {"amount": True, "status": "ready"}),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual(report["rows"], [])
        reasons = [(entry["seq"], entry["reason"], entry["column"]) for entry in report["quarantine"]]
        self.assertEqual(reasons, [
            (1, "invalid_amount", "amount"),
            (2, "invalid_amount", "amount"),
            (3, "invalid_amount", "amount"),
            (4, "invalid_amount", "amount"),
            (5, "invalid_amount", "amount"),
        ])
        # All five seqs were acknowledged even though none produced a row.
        self.assertEqual(report["cursor"], {"east/d1": 5})

    def test_unknown_version_does_not_inherit_another_source(self):
        sim = {
            "initial_rules": [{"source": "east", "schema": "v1", "rule": RULE_V1}],
            "events": [
                row(1, "west", "d1", 1, "a", "v1", 1, "upsert", {"amount": 1, "status": "ready"}),
                row(2, "east", "d1", 1, "b", "v2", 1, "upsert", {"amount": 1, "status": "ready"}),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual(report["rows"], [])
        self.assertEqual(
            [(entry["source"], entry["reason"], entry["column"]) for entry in report["quarantine"]],
            [("east", "unknown_schema", None), ("west", "unknown_schema", None)],
        )

    def test_newer_approval_wins_within_overlap(self):
        rule_v3 = {"amount_field": "amount_minor", "multiplier": 10,
                   "enum_field": "state", "enum": {"READY": "ready"}}
        sim = {
            "initial_rules": [],
            "events": [
                row(1, "east", "d1", 1, "a", "v9", 1, "upsert", {"amount_minor": 7, "state": "READY"}),
                approve(2, "east", "d1", "v9", RULE_V2, 1, 2),
                approve(3, "east", "d1", "v9", rule_v3, 1, 2),
                # seq 2 arrives after both approvals: the largest surviving
                # approval id selects the mapping.
                row(4, "east", "d1", 2, "b", "v9", 1, "upsert", {"amount_minor": 8, "state": "READY"}),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual({r["key"]: r["amount_cents"] for r in report["rows"]}, {"a": 7, "b": 80})
        # seq 1 was already backfilled by approval 2 and takes effect once.
        self.assertEqual([(b["seq"], b["approval_id"]) for b in report["backfilled"]], [(1, 2)])

    def test_revocation_stops_pending_backfill_of_that_approval_only(self):
        rule_v3 = {"amount_field": "amount_minor", "multiplier": 10,
                   "enum_field": "state", "enum": {"READY": "ready"}}
        sim = {
            "initial_rules": [],
            "events": [
                row(1, "east", "d1", 1, "a", "v9", 1, "upsert", {"amount_minor": 7, "state": "READY"}),
                approve(2, "east", "d1", "v9", RULE_V2, 1, 1),
                {"id": 3, "kind": "revoke", "approval_id": 2},
                row(4, "east", "d1", 2, "b", "v9", 1, "upsert", {"amount_minor": 8, "state": "READY"}),
                approve(5, "east", "d1", "v9", rule_v3, 2, 2),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        # seq 1 was already backfilled before the revoke; seq 2 uses approval 5.
        self.assertEqual(
            [(b["seq"], b["approval_id"]) for b in report["backfilled"]],
            [(1, 2), (2, 5)],
        )
        self.assertEqual({r["key"]: r["amount_cents"] for r in report["rows"]}, {"a": 7, "b": 80})

    def test_malformed_row_is_recorded_but_not_acknowledged(self):
        sim = {
            "initial_rules": [{"source": "east", "schema": "v1", "rule": RULE_V1}],
            "events": [
                row(1, "east", "d1", 1, "a", "v1", 1, "upsert", {"amount": 1, "status": "ready"}),
                {"id": 2, "kind": "row", "source": "east", "partition": "d1", "seq": 2,
                 "key": "b", "schema": "v1", "revision": 1, "op": "merge", "data": {}},
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual(report["cursor"], {"east/d1": 1})
        self.assertEqual([entry["action"] for entry in report["audit"]], ["accepted", "malformed_row"])

    def test_state_keeps_every_committed_partition_separately(self):
        sim = {
            "initial_rules": [{"source": "east", "schema": "v1", "rule": RULE_V1}],
            "events": [
                row(1, "east", "d1", 1, "a", "v1", 1, "upsert", {"amount": 1, "status": "ready"}),
                row(2, "east", "d2", 1, "b", "v1", 1, "upsert", {"amount": 2, "status": "ready"}),
                row(3, "east", "d2", 3, "c", "v1", 1, "upsert", {"amount": 3, "status": "ready"}),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual(report["cursor"], {"east/d1": 1, "east/d2": 1})
        self.assertEqual(len(report["rows"]), 3)

    def test_large_shuffled_replay_is_identical_to_ordered_run(self):
        events = []
        event_id = 1
        for source in ("east", "west", "north"):
            for seq in range(1, 21):
                events.append(row(event_id, source, "d1", seq, "k-%d" % (seq % 5),
                                  "v1" if source != "north" else "n1", seq // 3 + 1, "upsert",
                                  {"amount": seq, "status": "ready" if seq % 2 else "held"}))
                event_id += 1
        sim = {
            "initial_rules": [
                {"source": "east", "schema": "v1", "rule": RULE_V1},
                {"source": "west", "schema": "v1", "rule": RULE_V1},
            ],
            "events": events,
        }
        proc, reference = self.crawl(sim, name="ordered-large.json")
        self.assert_ran_clean(proc)

        shuffled = {"initial_rules": sim["initial_rules"],
                    "events": events[::2][::-1] + events[1::2][::-1]}
        proc, report = self.crawl(shuffled, name="shuffled-large.json")
        self.assert_ran_clean(proc)
        self.assertEqual(report, reference)
        # north has no mapping: every one of its 20 rows is quarantined but
        # acknowledged, so its cursor still reaches 20.
        self.assertEqual(report["cursor"]["north/d1"], 20)
        self.assertEqual(len([q for q in report["quarantine"] if q["source"] == "north"]), 20)


    def test_delete_with_unknown_version_is_quarantined_and_keeps_row(self):
        sim = {
            "initial_rules": [{"source": "east", "schema": "v1", "rule": RULE_V1}],
            "events": [
                row(1, "east", "d1", 1, "a", "v1", 1, "upsert", {"amount": 1, "status": "ready"}),
                row(2, "east", "d1", 2, "a", "v9", 2, "delete"),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        # The delete cannot be interpreted, so the key stays visible.
        self.assertEqual([r["key"] for r in report["rows"]], ["a"])
        self.assertEqual(report["tombstones"], [])
        self.assertEqual(report["quarantine"][0]["reason"], "unknown_schema")
        self.assertEqual(report["quarantine"][0]["seq"], 2)

    def test_quarantine_keeps_original_values_untouched(self):
        payload = {"amount": "12", "status": "ready", "extra": [1, 2, 3]}
        sim = {
            "initial_rules": [{"source": "east", "schema": "v1", "rule": RULE_V1}],
            "events": [row(1, "east", "d1", 1, "a", "v1", 1, "upsert", payload)],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual(report["quarantine"][0]["data"], payload)

    def test_rows_and_tombstones_are_sorted_by_key(self):
        sim = {
            "initial_rules": [
                {"source": "east", "schema": "v1", "rule": RULE_V1},
                {"source": "west", "schema": "v1", "rule": RULE_V1},
            ],
            "events": [
                row(1, "west", "d1", 1, "b", "v1", 1, "upsert", {"amount": 1, "status": "ready"}),
                row(2, "east", "d1", 1, "z", "v1", 1, "upsert", {"amount": 1, "status": "ready"}),
                row(3, "east", "d1", 2, "a", "v1", 1, "upsert", {"amount": 1, "status": "ready"}),
                row(4, "east", "d1", 3, "m", "v1", 1, "delete"),
                row(5, "east", "d1", 4, "n", "v1", 1, "upsert", {"amount": 2, "status": "held"}),
                row(6, "east", "d1", 5, "n", "v1", 2, "delete"),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        keys = [(r["source"], r["key"]) for r in report["rows"]]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(keys, [("east", "a"), ("east", "z"), ("west", "b")])
        tombstones = [(t["source"], t["key"]) for t in report["tombstones"]]
        self.assertEqual(tombstones, sorted(tombstones))
        # A delete is recorded even for a key never seen, so that a later
        # lower revision cannot bring it back.
        self.assertEqual(tombstones, [("east", "m"), ("east", "n")])


    def test_overlapping_approvals_pick_largest_id_for_rows_after_them(self):
        rule_v3 = {"amount_field": "amount_minor", "multiplier": 10,
                   "enum_field": "state", "enum": {"READY": "ready"}}
        sim = {
            "initial_rules": [],
            "events": [
                row(1, "east", "d1", 1, "a", "v9", 1, "upsert", {"amount_minor": 7, "state": "READY"}),
                approve(2, "east", "d1", "v9", RULE_V2, 1, 3),
                row(3, "east", "d1", 2, "b", "v9", 1, "upsert", {"amount_minor": 9, "state": "READY"}),
                approve(4, "east", "d1", "v9", rule_v3, 2, 3),
                row(5, "east", "d1", 3, "c", "v9", 1, "upsert", {"amount_minor": 5, "state": "READY"}),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        amounts = {r["key"]: r["amount_cents"] for r in report["rows"]}
        # seq 1 was backfilled by approval 2; seq 2 arrived while approval 2
        # was the newest one and stays committed at that value; seq 3 arrived
        # after approval 4 and uses the largest surviving approval id.
        self.assertEqual(amounts, {"a": 7, "b": 9, "c": 50})
        self.assertEqual([(b["seq"], b["approval_id"]) for b in report["backfilled"]], [(1, 2)])

    def test_later_approval_backfills_rows_quarantined_before_any_approval(self):
        sim = {
            "initial_rules": [],
            "events": [
                row(1, "east", "d1", 1, "a", "v9", 1, "upsert", {"amount_minor": 7, "state": "READY"}),
                row(2, "east", "d1", 2, "b", "v9", 1, "upsert", {"amount_minor": 9, "state": "READY"}),
                approve(3, "east", "d1", "v9", RULE_V2, 1, 2),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual({r["key"]: r["amount_cents"] for r in report["rows"]}, {"a": 7, "b": 9})
        # Both rows were backfilled in ascending seq order.
        self.assertEqual([b["seq"] for b in report["backfilled"]], [1, 2])

    def test_approval_never_applies_to_a_different_version(self):
        sim = {
            "initial_rules": [],
            "events": [
                row(1, "east", "d1", 1, "a", "v9", 1, "upsert", {"amount_minor": 7, "state": "READY"}),
                row(2, "east", "d1", 2, "b", "v8", 1, "upsert", {"amount_minor": 8, "state": "READY"}),
                approve(3, "east", "d1", "v9", RULE_V2, 1, 2),
            ],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual([r["key"] for r in report["rows"]], ["a"])
        # The v8 row is still unknown: the v9 approval does not cover it.
        self.assertEqual([(q["seq"], q["schema"]) for q in report["quarantine"]], [(2, "v8")])

    def test_duplicate_capture_under_new_event_id_is_processed_once(self):
        base = row(1, "east", "d1", 1, "a", "v1", 1, "upsert", {"amount": 1, "status": "ready"})
        duplicate = dict(base, id=2)
        sim = {
            "initial_rules": [{"source": "east", "schema": "v1", "rule": RULE_V1}],
            "events": [base, duplicate],
        }
        proc, report = self.crawl(sim)
        self.assert_ran_clean(proc)
        self.assertEqual(report["rows"], [{"source": "east", "partition": "d1", "key": "a",
                                           "revision": 1, "amount_cents": 100, "status": "ready"}])
        self.assertEqual(report["conflicts"], [])
        # Only the first capture is recorded as a decision.
        self.assertEqual([e["action"] for e in report["audit"]], ["accepted"])

    def test_backfilled_row_takes_effect_exactly_once(self):
        sim = scenario()
        state = self.tmp / "once.log"
        proc, _ = self.crawl(sim, name="once-a.json", state=state)
        self.assert_ran_clean(proc)
        first = json.loads((self.tmp / "once-a.json").read_text(encoding="utf-8"))
        self.assertEqual(len(first["backfilled"]), 1)

        # Replay the same input (including the approval) against the state.
        proc, _ = self.crawl(sim, name="once-b.json", state=state)
        self.assert_ran_clean(proc)
        second = json.loads((self.tmp / "once-b.json").read_text(encoding="utf-8"))
        self.assertEqual(second, first)
        self.assertEqual(len(second["backfilled"]), 1)


if __name__ == "__main__":
    unittest.main()
