#!/usr/bin/env python3
"""Acceptance checks for bitemporal.py.

The oracle does not reuse any engine code.  It replays the raw event stream
in ``(system, version)`` order, maintaining one believed dimension event per
``(entity, business instant)`` and one winning fact event per fact id, and
snapshots that replay state at each requested system cut point.  The engine
answers queries by scanning the log with max-``(system, version)`` selection
and keeps an incremental materialized view, so agreement between the two is
evidence that both axes and the revision rules are implemented.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bitemporal.py"
sys.path.insert(0, str(ROOT))
import bitemporal  # noqa: E402


def okey(event):
    return (event["system"], event["version"])


# ---------------------------------------------------------------------------
# independent oracle


def oracle_snapshots(events, cutpoints):
    """Replay the event log and snapshot at every cut point."""
    entities = sorted({e["entity"] for e in events})
    instants = sorted({e["at"] for e in events if e["type"] == "fact"})
    ordered = sorted(events, key=okey)

    believed = {}
    facts = {}
    snapshots = {}
    position = 0
    for cut in sorted(cutpoints):
        while position < len(ordered) and ordered[position]["system"] <= cut:
            event = ordered[position]
            position += 1
            if event["type"] == "fact":
                facts[event["id"]] = event
                continue
            for at in instants:
                end = event["to"]
                if event["from"] <= at and (end is None or at < end):
                    believed[(event["entity"], at)] = event

        rows = []
        for fact_id in sorted(facts):
            fact = facts[fact_id]
            if fact.get("op") == "delete":
                continue
            event = believed.get((fact["entity"], fact["at"]))
            rows.append(oracle_row(fact_id, fact, event))
        snapshots[cut] = rows
    return snapshots


def oracle_row(fact_id, fact, event):
    base = {
        "id": fact_id,
        "fact_version": fact["version"],
        "entity": fact["entity"],
        "at": fact["at"],
    }
    if event is None:
        base.update(dimension_version=None, label=None, reason="no_dimension")
    elif event.get("op") == "remove":
        base.update(dimension_version=None, label=None, reason="removed")
    else:
        base.update(
            dimension_version=event["version"],
            label=event.get("label"),
            reason="matched",
        )
    return base


def rows_by_id(rows):
    return {row["id"]: row for row in rows}


# ---------------------------------------------------------------------------
# CLI harness


def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, str(CLI)] + [str(arg) for arg in args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    return proc.returncode, proc.stdout, proc.stderr


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.db"
        self.payloads = 0
        self.applied = []

    def tearDown(self):
        self.tmp.cleanup()

    def reset(self):
        """Start a fresh state file (several scenarios in one test)."""
        self.state = Path(self.tmp.name) / ("state-%d.db" % self.payloads)
        self.applied = []

    def apply(self, events, expect_code=0, details=False):
        self.payloads += 1
        payload = Path(self.tmp.name) / ("batch-%d.json" % self.payloads)
        payload.write_text(json.dumps({"events": events}), encoding="utf-8")
        args = ["apply", self.state, payload]
        if details:
            args.append("--details")
        code, out, err = run_cli(*args)
        self.assertEqual(code, expect_code, err)
        if code != 0:
            return None
        result = json.loads(out)
        self.applied.extend(events)
        return result

    def query(self, asof):
        code, out, err = run_cli("query", self.state, "--asof", asof)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def dimensions(self, asof, at, entity):
        code, out, err = run_cli("dimensions", self.state, "--asof", asof, "--at", at)
        self.assertEqual(code, 0, err)
        rows = {row["entity"]: row for row in json.loads(out)["rows"]}
        self.assertIn(entity, rows)
        return rows[entity]

    def fact(self, fact_id, asof):
        code, out, err = run_cli("fact", self.state, fact_id, "--asof", asof)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def log(self):
        code, out, err = run_cli("log", self.state)
        self.assertEqual(code, 0, err)
        return json.loads(out)["batches"]

    def publish(self, asof=None, batch=None):
        args = ["publish", self.state]
        if asof is not None:
            args += ["--asof", asof]
        else:
            args += ["--batch", batch]
        code, out, err = run_cli(*args)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def history_rows(self, asof=None):
        args = ["history", self.state]
        if asof is not None:
            args += ["--asof", asof]
        code, out, err = run_cli(*args)
        self.assertEqual(code, 0, err)
        return json.loads(out)["rows"]

    def unmatched(self, asof):
        code, out, err = run_cli("unmatched", self.state, "--asof", asof)
        self.assertEqual(code, 0, err)
        return json.loads(out)["rows"]


# ---------------------------------------------------------------------------
# the 8-revision scenario

def fact(fid, entity, at, system, version):
    return {
        "type": "fact",
        "id": fid,
        "entity": entity,
        "at": at,
        "system": system,
        "version": version,
    }


def dim(entity, start, end, system, version, label=None, op="upsert"):
    event = {
        "type": "dimension",
        "entity": entity,
        "from": start,
        "to": end,
        "system": system,
        "version": version,
        "op": op,
    }
    if label is not None:
        event["label"] = label
    return event


# 12 facts, several of them on dimension interval boundaries.  Eight of the
# dimension events below restate a business range that earlier reports had
# already reported (system time later than the facts they affect).
SCENARIO = [
    # base facts
    fact("f01", "north", 5, 1, "f01-v1"),
    fact("f02", "north", 7, 2, "f02-v1"),
    fact("f03", "north", 10, 3, "f03-v1"),
    fact("f04", "south", 3, 1, "f04-v1"),
    fact("f05", "south", 8, 4, "f05-v1"),
    fact("f06", "south", 12, 5, "f06-v1"),
    fact("f07", "east", 1, 0, "f07-v1"),
    fact("f08", "east", 8, 6, "f08-v1"),
    fact("f09", "east", 23, 6, "f09-v1"),
    fact("f10", "west", 4, 2, "f10-v1"),
    fact("f11", "west", 20, 7, "f11-v1"),
    fact("f12", "west", 24, 8, "f12-v1"),
    # base dimensions
    dim("north", 0, None, 0, "north-0", "north-base"),
    dim("south", 0, None, 1, "south-0", "south-base"),
    dim("east", 0, 6, 0, "east-0", "east-base"),
    dim("west", 0, None, 0, "west-0", "west-base"),
    # retroactive revision 1: split [5,10) out of the north default
    dim("north", 5, 10, 2, "north-1", "north-q1"),
    # retroactive revision 2: revoke that split
    dim("north", 5, 10, 4, "north-2", None, op="remove"),
    # retroactive revision 3: re-claim the revoked range
    dim("north", 5, 10, 6, "north-3", "north-q1-fix"),
    # retroactive revision 4: south promo window
    dim("south", 8, 12, 3, "south-1", "south-promo"),
    # retroactive revision 5: revoke the promo window
    dim("south", 8, 12, 7, "south-2", None, op="remove"),
    # retroactive revision 6: east second half
    dim("east", 16, 20, 4, "east-1", "east-q3"),
    # retroactive revisions 7 and 8: west window, same system time, the
    # lexicographically greater version wins
    dim("west", 20, 24, 5, "west-1a", "west-draft"),
    dim("west", 20, 24, 5, "west-1b", "west-final"),
    # later revocation of the west window
    dim("west", 20, 24, 10, "west-2", None, op="remove"),
    # fact corrections: delete then restore f03, move f11
    {
        "type": "fact",
        "id": "f03",
        "entity": "north",
        "at": 10,
        "system": 9,
        "version": "f03-del",
        "op": "delete",
    },
    fact("f03", "north", 10, 11, "f03-v2"),
    fact("f11", "west", 21, 12, "f11-v2"),
]

SCENARIO_CUTS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12]


def split_batches(events, plan):
    """Partition events into batches following a list of batch sizes."""
    ordered = sorted(events, key=okey)
    batches = []
    position = 0
    for size in plan:
        batches.append(ordered[position : position + size])
        position += size
    if position < len(ordered):
        batches.append(ordered[position:])
    return batches


def manual_scope(before_rows, accepted):
    """Hand-computed impact scope for one batch.

    * a fact is in scope when one of its own accepted events changes the
      winning fact version, or
    * when an accepted dimension event of its entity covers its business
      instant.
    """
    scope = set()
    fact_ids = {e["id"] for e in accepted if e["type"] == "fact"}
    scope |= fact_ids
    current = rows_by_id(before_rows)
    for event in accepted:
        if event["type"] != "dimension":
            continue
        end = event["to"]
        for fact_id, row in current.items():
            if row["entity"] != event["entity"]:
                continue
            if row["at"] < event["from"]:
                continue
            if end is not None and row["at"] >= end:
                continue
            scope.add(fact_id)
    return scope


# ---------------------------------------------------------------------------
# acceptance 1: replay vs independent full rebuild on 12 cut points

class BitmeporalTableTests(Case):
    def test_explicit_dual_intervals(self):
        self.apply(
            [
                dim("n", 0, None, 0, "d0", "base"),
                dim("n", 5, 10, 2, "d1", "split"),
                dim("n", 5, 10, 3, "d2", None, op="remove"),
                dim("n", 5, 10, 4, "d3", "reclaimed"),
            ]
        )
        rows = self.history_rows()

        # d0 owns [0,5) and [10,infty) forever.  On [5,10) it is believed
        # from system 0 until 2, when the split first becomes known.
        base = [r for r in rows if r["version"] == "d0"]
        self.assertEqual(
            [
                (r["business_from"], r["business_to"], r["system_from"], r["system_to"])
                for r in base
            ],
            [
                (0, 5, 0, None),
                (5, 10, 0, 2),
                (10, None, 0, None),
            ],
        )

        # The split/remove/re-claim chain owns [5,10) in successive system
        # windows, which is exactly one row each.
        chain = [
            (r["version"], r["system_from"], r["system_to"], r["op"])
            for r in rows
            if (r["business_from"], r["business_to"]) == (5, 10)
            and r["version"] != "d0"
        ]
        chain.sort(key=lambda item: item[1])
        self.assertEqual(
            chain,
            [
                ("d1", 2, 3, "upsert"),
                ("d2", 3, 4, "remove"),
                ("d3", 4, None, "upsert"),
            ],
        )

    def test_partial_overlap_splits_the_earlier_row(self):
        self.apply(
            [
                dim("n", 0, 20, 0, "wide", "wide"),
                dim("n", 5, 10, 2, "narrow", "narrow"),
            ]
        )
        rows = {row["version"]: row for row in self.history_rows()}
        # The wide row keeps both flanks and still owns the middle until the
        # narrow revision is known; only the middle's authority is cut short.
        self.assertEqual(
            sorted(
                (r["business_from"], r["business_to"], r["system_from"], r["system_to"])
                for r in self.history_rows()
                if r["version"] == "wide"
            ),
            [(0, 5, 0, None), (5, 10, 0, 2), (10, 20, 0, None)],
        )
        narrow = [
            r for r in self.history_rows() if r["version"] == "narrow"
        ]
        self.assertEqual(
            [
                (r["business_from"], r["business_to"], r["system_from"], r["system_to"])
                for r in narrow
            ],
            [(5, 10, 2, None)],
        )

    def test_history_visibility_is_half_open(self):
        self.apply(
            [
                dim("n", 0, None, 0, "d0", "base"),
                dim("n", 0, None, 2, "d1", "new"),
            ]
        )
        self.assertEqual(
            sorted(r["version"] for r in self.history_rows(1)), ["d0"]
        )
        self.assertEqual(
            sorted(r["version"] for r in self.history_rows(2)), ["d1"]
        )
        self.assertEqual(
            sorted(r["version"] for r in self.history_rows(0)), ["d0"]
        )

    def test_same_system_time_conflict_is_flagged(self):
        self.apply(
            [
                dim("n", 0, None, 5, "a", "low"),
                dim("n", 0, None, 5, "c", "high"),
                dim("n", 0, None, 5, "b", "mid"),
            ]
        )
        rows = {row["version"]: row for row in self.history_rows()}
        self.assertTrue(rows["a"]["shadowed"])
        self.assertTrue(rows["b"]["shadowed"])
        self.assertFalse(rows["c"]["shadowed"])
        # Losers have an empty system interval: never authoritative.
        self.assertEqual(
            (rows["a"]["system_from"], rows["a"]["system_to"]), (5, 5)
        )
        self.assertEqual(
            (rows["b"]["system_from"], rows["b"]["system_to"]), (5, 5)
        )
        self.assertEqual(
            (rows["c"]["system_from"], rows["c"]["system_to"]), (5, None)
        )

    def test_non_overlapping_revisions_do_not_close_each_other(self):
        self.apply(
            [
                dim("south", 0, 10, 0, "a", "A"),
                dim("north", 0, 10, 0, "b", "B"),
                dim("south", 20, 30, 1, "c", "C"),
            ]
        )
        rows = {row["version"]: row for row in self.history_rows()}
        self.assertIsNone(rows["a"]["system_to"])
        self.assertIsNone(rows["b"]["system_to"])
        self.assertIsNone(rows["c"]["system_to"])

    def test_adjacent_touching_ranges_are_not_overlaps(self):
        self.apply(
            [
                dim("n", 0, 10, 0, "a", "A"),
                dim("n", 10, 20, 1, "b", "B"),
            ]
        )
        rows = {row["version"]: row for row in self.history_rows()}
        self.assertIsNone(rows["a"]["system_to"])
        self.assertIsNone(rows["b"]["system_to"])

    def test_history_agrees_with_per_instant_selection(self):
        """The table must answer every (system, business) pair correctly."""
        self.apply(SCENARIO)
        rows = self.history_rows()
        latest = max(e["system"] for e in SCENARIO)
        for system in range(0, latest + 1):
            for at in range(0, 26):
                covering = [
                    row
                    for row in rows
                    if row["business_from"] <= at
                    and (row["business_to"] is None or at < row["business_to"])
                    and row["system_from"] <= system
                    and (row["system_to"] is None or system < row["system_to"])
                    and not row["shadowed"]
                ]
                entities = {row["entity"] for row in covering}
                # At most one authoritative row per entity at any point.
                for entity in entities:
                    owners = [row for row in covering if row["entity"] == entity]
                    self.assertEqual(
                        len(owners),
                        1,
                        "ambiguous history for %r at system %d business %d: %r"
                        % (entity, system, at, owners),
                    )
                    winner = bitemporal.pick_dimension(
                        bitemporal.index_events(SCENARIO)[1][entity], at, system
                    )
                    expected = None if winner is None else winner["version"]
                    self.assertEqual(owners[0]["version"], expected)

    def test_unmatched_reasons_for_historical_report(self):
        self.apply(
            [
                fact("a", "n", 4, 3, "fa"),
                fact("b", "n", 9, 3, "fb"),
                dim("n", 0, 5, 0, "d0", "base"),
                dim("n", 0, 5, 2, "d1", None, op="remove"),
            ]
        )
        early = {row["at"]: row for row in self.unmatched(1)}
        self.assertEqual(early[4]["reason"], "matched")
        self.assertEqual(early[4]["dimension_version"], "d0")
        self.assertEqual(early[9]["reason"], "no_dimension")

        late = {row["at"]: row for row in self.unmatched(2)}
        self.assertEqual(late[4]["reason"], "removed")
        self.assertEqual(
            [c["version"] for c in late[4]["candidates"]], ["d0", "d1"]
        )
        self.assertEqual(late[9]["candidates"], [])


class RandomizedEquivalence(Case):
    """Property tests over generated revisions, splits, removals and backfills."""

    def scenario(self, seed):
        import random

        rng = random.Random(seed)
        entities = ["e%d" % i for i in range(3)]
        events = []
        for index in range(12):
            events.append(
                fact(
                    "f%02d" % index,
                    rng.choice(entities),
                    rng.randrange(0, 24),
                    rng.randrange(0, 9),
                    "fx%02d" % index,
                )
            )
        version = 0
        for system in range(0, 9):
            for _ in range(rng.randrange(1, 4)):
                version += 1
                entity = rng.choice(entities)
                start = rng.randrange(0, 24)
                end = rng.choice([None, start, start + rng.randrange(1, 12)])
                op = "remove" if rng.random() < 0.25 else "upsert"
                events.append(
                    dim(
                        entity,
                        start,
                        end,
                        system,
                        "dv%03d" % version,
                        None if op == "remove" else "l%d" % version,
                        op,
                    )
                )
        # a couple of same-system-time conflicts
        events.append(dim("e0", 0, None, 4, "conflict-a", "A"))
        events.append(dim("e0", 0, None, 4, "conflict-b", "B"))
        events.append(fact("f00", "e1", 3, 1, "f00-v2"))
        return events

    def test_incremental_matches_oracle_for_many_seeds(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                self.reset()
                events = self.scenario(seed)
                cuts = sorted({e["system"] for e in events}) + [99]
                expected = oracle_snapshots(events, cuts)
                shuffled = list(events)
                import random

                random.Random(seed).shuffle(shuffled)
                size = 1 + seed % 5
                for start in range(0, len(shuffled), size):
                    self.apply(shuffled[start : start + size])
                for cut in cuts:
                    self.assertEqual(
                        rows_by_id(self.query(cut)["rows"]),
                        rows_by_id(expected[cut]),
                        "seed %d cut %d" % (seed, cut),
                    )
                code, out, err = run_cli("verify", self.state)
                self.assertEqual(code, 0, out + err)

    def test_scope_is_exactly_the_rows_that_change(self):
        import random

        for seed in range(8):
            rng = random.Random(1000 + seed)
            with self.subTest(seed=seed):
                self.reset()
                events = self.scenario(seed)
                order = sorted(events, key=okey)
                rng.shuffle(order)
                size = 1 + seed % 4
                for start in range(0, len(order), size):
                    batch = order[start : start + size]
                    before = rows_by_id(self.query(self._current_asof())["rows"])
                    result = self.apply(batch, details=True)
                    after = rows_by_id(self.query(self._current_asof())["rows"])

                    actually_changed = {
                        key
                        for key in set(before) | set(after)
                        if before.get(key) != after.get(key)
                    }
                    self.assertTrue(
                        actually_changed <= set(result["affected"]),
                        "scope missed changed keys for seed %d" % seed,
                    )
                    self.assertEqual(
                        {c["id"] for c in result["changes"]}, actually_changed
                    )

    def _current_asof(self):
        if not self.applied:
            return -1
        return max(e["system"] for e in self.applied)


class ReplayMatchesIndependentRebuild(Case):
    def test_twelve_cutpoints_match_oracle(self):
        batches = split_batches(SCENARIO, [4, 3, 5, 2, 6, 4, 5, 3])
        applied = []
        for batch in batches:
            self.apply(batch)
            applied.extend(batch)

        expected = oracle_snapshots(applied, SCENARIO_CUTS)

        for cut in SCENARIO_CUTS:
            actual = self.query(cut)
            self.assertEqual(actual["asof"], cut)
            self.assertEqual(
                rows_by_id(actual["rows"]),
                rows_by_id(expected[cut]),
                "system cut %r disagrees with full rebuild" % cut,
            )

    def test_boundary_points_of_adjacent_intervals(self):
        self.apply(SCENARIO)
        latest = max(e["system"] for e in SCENARIO)

        # north [5,10) sits between the default row and its own boundary.
        north = rows_by_id(self.query(latest)["rows"])
        self.assertEqual(north["f02"]["dimension_version"], "north-3")  # at 7
        self.assertEqual(north["f03"]["dimension_version"], "north-0")  # at 10
        self.assertEqual(north["f01"]["dimension_version"], "north-3")  # at 5

        # The same business instant changes answer when the system axis moves.
        self.assertEqual(
            rows_by_id(self.query(3)["rows"])["f02"]["dimension_version"], "north-1"
        )
        self.assertEqual(
            rows_by_id(self.query(4)["rows"])["f02"]["reason"], "removed"
        )
        self.assertEqual(
            rows_by_id(self.query(6)["rows"])["f02"]["dimension_version"], "north-3"
        )

        # south [8,12): at=8 inside, at=12 outside.
        south = rows_by_id(self.query(latest)["rows"])
        self.assertEqual(south["f05"]["reason"], "removed")  # at 8, revoked at 7
        self.assertEqual(south["f06"]["dimension_version"], "south-0")  # at 12

        # west [20,24): at=20 inside, at=24 outside.
        west = rows_by_id(self.query(latest)["rows"])
        self.assertEqual(west["f11"]["reason"], "removed")  # at 21 after move
        self.assertEqual(west["f12"]["dimension_version"], "west-0")  # at 24

    def test_prior_system_time_keeps_old_dimension_version(self):
        self.apply(SCENARIO)
        latest = max(e["system"] for e in SCENARIO)
        self.assertEqual(
            self.dimensions(latest, 7, "north")["dimension_version"], "north-3"
        )
        self.assertEqual(
            self.dimensions(3, 7, "north")["dimension_version"], "north-1"
        )
        self.assertEqual(
            self.dimensions(3, 7, "north")["candidates"][-1]["version"], "north-1"
        )


# ---------------------------------------------------------------------------
# acceptance 1b: revision semantics

class RevisionSemantics(Case):
    def test_split_remove_reclaim(self):
        self.apply([dim("n", 0, None, 0, "d0", "base")])
        fact_ = fact("x", "n", 7, 1, "fx")
        self.apply([fact_])
        self.assertEqual(self.query(1)["rows"][0]["label"], "base")

        self.apply([dim("n", 5, 10, 2, "d1", "split")])
        self.assertEqual(self.query(2)["rows"][0]["dimension_version"], "d1")

        self.apply([dim("n", 5, 10, 3, "d2", None, op="remove")])
        row = self.query(3)["rows"][0]
        self.assertEqual((row["reason"], row["label"]), ("removed", None))
        # The old report at system time 2 still shows the split.
        self.assertEqual(self.query(2)["rows"][0]["dimension_version"], "d1")

        self.apply([dim("n", 5, 10, 4, "d3", "reclaimed")])
        self.assertEqual(self.query(4)["rows"][0]["dimension_version"], "d3")

    def test_gap_reports_no_dimension(self):
        self.apply([fact("x", "n", 7, 1, "fx")])
        row = self.query(1)["rows"][0]
        self.assertEqual((row["dimension_version"], row["reason"]), (None, "no_dimension"))
        info = self.dimensions(1, 7, "n")
        self.assertEqual((info["dimension_version"], info["reason"], info["candidates"]),
                         (None, "no_dimension", []))

    def test_same_system_higher_version_wins(self):
        self.apply(
            [
                dim("n", 0, None, 2, "v-a", "low"),
                dim("n", 0, None, 2, "v-c", "high"),
                dim("n", 0, None, 2, "v-b", "middle"),
                fact("x", "n", 1, 1, "fx"),
            ]
        )
        self.assertEqual(self.query(2)["rows"][0]["dimension_version"], "v-c")
        self.assertEqual(
            [c["version"] for c in self.dimensions(2, 1, "n")["candidates"]],
            ["v-a", "v-b", "v-c"],
        )

    def test_later_system_beats_greater_version(self):
        # Ordering is (system, version); system dominates.
        self.apply(
            [
                dim("n", 0, None, 2, "zzz", "old-but-high-version"),
                dim("n", 0, None, 3, "aaa", "new"),
            ]
        )
        self.assertEqual(self.dimensions(3, 1, "n")["dimension_version"], "aaa")
        self.assertEqual(self.dimensions(2, 1, "n")["dimension_version"], "zzz")

    def test_delete_and_restore_fact(self):
        self.apply([fact("x", "n", 1, 1, "fx1"), dim("n", 0, None, 0, "d0", "base")])
        self.apply([dict(fact("x", "n", 1, 2, "fx2"), op="delete")])
        self.assertEqual(self.query(2)["rows"], [])
        self.assertEqual(len(self.query(1)["rows"]), 1)
        self.apply([fact("x", "n", 1, 3, "fx3")])
        self.assertEqual(self.query(3)["rows"][0]["fact_version"], "fx3")

    def test_fact_move_reassociates(self):
        self.apply(
            [
                dim("a", 0, None, 0, "da", "A"),
                dim("b", 0, None, 0, "db", "B"),
                fact("x", "a", 1, 1, "fx1"),
            ]
        )
        self.assertEqual(self.query(1)["rows"][0]["label"], "A")
        result = self.apply([fact("x", "b", 1, 2, "fx2")])
        self.assertEqual(result["affected"], ["x"])
        self.assertEqual(self.query(2)["rows"][0]["label"], "B")


# ---------------------------------------------------------------------------
# acceptance 2: batch-order independence, idempotent replay, impact scope

class BatchIndependence(Case):
    def test_current_and_history_independent_of_batching(self):
        baseline_batches = split_batches(SCENARIO, [len(SCENARIO)])
        for batch in baseline_batches:
            self.apply(batch)
        baseline_current = self.query(max(e["system"] for e in SCENARIO))
        baseline_history = {c: self.query(c) for c in SCENARIO_CUTS}

        plans = [[1] * len(SCENARIO), [5, 1, 7, 2, 3, 4, 9], [13, 2, 11]]
        for index, plan in enumerate(plans):
            with self.subTest(plan=plan):
                other = Path(self.tmp.name) / ("state-%d.db" % index)
                saved, self.state = self.state, other
                try:
                    for batch in split_batches(SCENARIO, plan):
                        self.apply(batch)
                    self.assertEqual(
                        self.query(max(e["system"] for e in SCENARIO)),
                        baseline_current,
                    )
                    for cut in SCENARIO_CUTS:
                        self.assertEqual(self.query(cut), baseline_history[cut])
                finally:
                    self.state = saved

    def test_repeating_a_revision_is_idempotent(self):
        self.apply(SCENARIO)
        latest = max(e["system"] for e in SCENARIO)
        before = self.query(latest)
        history = {c: self.query(c) for c in SCENARIO_CUTS}

        # replay the whole log again, then replay one single revision again
        for batch in split_batches(SCENARIO, [6, 6, 6, 6]):
            result = self.apply(batch)
            self.assertEqual(result["accepted"], 0)
            self.assertEqual(result["affected"], [])
        again = self.apply([e for e in SCENARIO if e["version"] == "north-3"])
        self.assertEqual(again, {"accepted": 0, "affected": []})

        self.assertEqual(self.query(latest), before)
        for cut in SCENARIO_CUTS:
            self.assertEqual(self.query(cut), history[cut])
        # Replays must not append change-log entries.
        self.assertEqual(len(self.log()), 1)

    def test_duplicate_then_conflict_in_same_batch(self):
        event = fact("x", "n", 1, 1, "fx")
        conflict = dict(event, at=2)
        self.assertEqual(
            self.apply([event, event])["accepted"], 1
        )
        self.apply([conflict], expect_code=2)
        self.apply([event, conflict], expect_code=2)

    def test_affected_matches_hand_computed_scope(self):
        plan = [4, 3, 5, 2, 6, 4, 5, 3]
        batches = split_batches(SCENARIO, plan)
        prior_rows = []
        for batch in batches:
            expected = manual_scope(prior_rows, batch)
            result = self.apply(batch, details=True)
            self.assertEqual(
                result["affected"],
                sorted(expected),
                "wrong impact scope for %r" % [e["version"] for e in batch],
            )
            cut = max(e["system"] for e in self.applied)
            prior_rows = self.query(cut)["rows"]

    def test_affected_is_not_cumulative(self):
        self.apply([dim("n", 0, None, 0, "d0", "base")])
        self.apply(
            [
                fact("x", "n", 1, 1, "fx"),
                fact("y", "n", 2, 1, "fy"),
                fact("z", "m", 2, 1, "fz"),
            ]
        )
        self.assertEqual(self.apply([dim("m", 0, None, 2, "dm", "M")])["affected"], ["z"])
        # A batch touching nothing returns an empty scope, not the previous one.
        self.assertEqual(self.apply([fact("w", "n", 99, 3, "fw")])["affected"], ["w"])
        self.assertEqual(self.apply([dim("q", 100, 200, 4, "dq", "Q")])["affected"], [])

    def test_affected_only_lists_rows_that_changed_either_way(self):
        self.apply([fact("x", "n", 1, 1, "fx"), dim("n", 0, None, 0, "d0", "base")])
        # A dimension revision whose business range contains no fact still
        # falls in the impact scope but changes nothing.
        result = self.apply([dim("n", 50, 60, 2, "d1", "far")], details=True)
        self.assertEqual(result["affected"], [])
        self.assertEqual(result["changes"], [])

    def test_removed_reason_is_explained(self):
        self.apply([fact("x", "n", 7, 1, "fx"), dim("n", 0, 10, 2, "d1", "a")])
        self.apply([dim("n", 0, 10, 3, "d2", None, op="remove")])
        detail = self.fact("x", 3)
        self.assertEqual(detail["row"]["reason"], "removed")
        self.assertEqual(
            [c["version"] for c in detail["dimension_candidates"]], ["d1", "d2"]
        )

    def test_published_digests_stay_reproducible(self):
        for batch in split_batches(SCENARIO, [5, 7, 6, 8, 9]):
            self.apply(batch)
            code, out, err = run_cli("verify", self.state)
            self.assertEqual(code, 0, out + err)
            self.assertTrue(json.loads(out)["ok"])
        entries = self.log()
        self.assertEqual(len(entries), 5)
        for entry in entries:
            published = entry["published"]
            issued = self.publish(batch=entry["batch"])
            self.assertEqual(issued["digest"], published["digest"])
            self.assertEqual(bitemporal.report_digest(issued["rows"]), published["digest"])
            self.assertEqual(len(issued["rows"]), published["fact_count"])

    def test_backfill_changes_recomputation_but_not_issued_report(self):
        # Publish a report at system time 1, then backfill a dimension event
        # that is dated system time 0 well after the fact.
        self.apply([fact("x", "n", 7, 1, "fx"), dim("n", 0, 5, 0, "d0", "base")])
        issued = self.publish(asof=1)
        self.assertEqual(issued["rows"][0]["reason"], "no_dimension")

        self.apply([dim("n", 0, 10, 0, "d-backfill", "backfilled")])

        # Today's recomputation for the same as-of sees the backfilled row...
        recomputed = self.query(1)
        self.assertEqual(recomputed["rows"][0]["dimension_version"], "d-backfill")
        # ...while the report that was actually issued is unchanged.
        again = self.publish(asof=1)
        self.assertEqual(again["rows"], issued["rows"])
        self.assertEqual(again["digest"], issued["digest"])
        # verify keeps both facts distinct and still passes.
        code, out, err = run_cli("verify", self.state)
        self.assertEqual(code, 0, out + err)
        report_ = json.loads(out)
        self.assertTrue(report_["published"][0]["backfilled"])
        self.assertFalse(report_["published"][-1]["backfilled"])

    def test_verify_detects_a_rewritten_report(self):
        self.apply(SCENARIO[:8])
        state = json.loads(self.state.read_text())
        state["log"][0]["published"]["rows"][0]["label"] = "tampered"
        self.state.write_text(json.dumps(state))
        code, out, err = run_cli("verify", self.state)
        self.assertEqual(code, 1, out + err)
        self.assertFalse(json.loads(out)["ok"])


# ---------------------------------------------------------------------------
# state handling

class StateHandling(Case):
    def test_rebuilds_current_view_when_stale(self):
        self.apply([fact("x", "n", 1, 1, "fx"), dim("n", 0, None, 0, "d0", "base")])
        state = json.loads(self.state.read_text())
        state["current"] = {"asof": None, "rows": []}
        self.state.write_text(json.dumps(state))
        self.assertEqual(
            self.query(1)["rows"][0]["dimension_version"], "d0"
        )

    def test_accepts_legacy_state_file(self):
        self.state.write_text(json.dumps({"events": SCENARIO[:6]}))
        self.assertEqual(len(self.query(2)["rows"]), 3)
        self.apply(SCENARIO[6:])
        self.assertTrue(json.loads(self.state.read_text())["current"]["rows"])

    def test_invalid_input_exits_2(self):
        self.apply([{"type": "fact", "id": "x"}], expect_code=2)
        self.apply([dim("n", 5, 3, 0, "bad")], expect_code=2)
        self.apply([fact("x", "n", 1, 1, "")], expect_code=2)
        self.apply([fact("x", "n", 1, -1, "v")], expect_code=2)

    def test_state_survives_failed_apply(self):
        self.apply(SCENARIO[:5])
        before = self.state.read_text()
        self.apply([dim("n", 5, 3, 0, "bad")], expect_code=2)
        self.assertEqual(self.state.read_text(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
