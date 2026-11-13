"""Acceptance tests for bizday.py: DST folds/gaps, half-hour switches,
midnight adjacency, out-of-order replay, corrections, voids, versioning
and scoped recomputation on tz_version switches. Run: python3 test_bizday.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIZDAY = Path(__file__).with_name("bizday.py")

REGIONS_V1 = {
    "east": {"initial_offset_min": 480, "transitions": []},
    "eu": {"initial_offset_min": 60, "transitions": [
        {"at_utc": "2026-03-29T01:00:00Z", "offset_min": 120},
        {"at_utc": "2026-10-25T01:00:00Z", "offset_min": 60}]},
    "lh": {"initial_offset_min": 630, "transitions": [
        {"at_utc": "2026-10-04T15:30:00Z", "offset_min": 660},
        {"at_utc": "2027-04-04T15:30:00Z", "offset_min": 630}]},
}

BATCH1 = {"tz_version": "v1", "regions": REGIONS_V1, "events": [
    {"id": "e1", "rev": 1, "region": "east",
     "local": "2026-11-12T00:10:00", "amount": "1.35"},
    {"id": "n1", "rev": 1, "region": "eu",
     "local": "2026-03-28T12:00:00", "amount": "1.00"},
    {"id": "s1", "rev": 1, "region": "eu",
     "local": "2026-03-29T01:59:59", "amount": "2.00"},
    {"id": "s2", "rev": 1, "region": "eu",
     "local": "2026-03-29T03:00:00", "amount": "3.00"},
    {"id": "m2", "rev": 1, "region": "eu",
     "local": "2026-10-24T23:59:59", "amount": "0.75"},
    {"id": "m1", "rev": 1, "region": "eu",
     "local": "2026-10-25T00:00:00", "amount": "0.25"},
    {"id": "f1", "rev": 1, "region": "eu", "local": "2026-10-25T02:30:00",
     "fold": 0, "amount": "1.50"},
    {"id": "f2", "rev": 1, "region": "eu", "local": "2026-10-25T02:30:00",
     "fold": 1, "amount": "2.50"},
    {"id": "h1", "rev": 1, "region": "lh",
     "local": "2026-10-05T03:00:00", "amount": "4.00"},
    {"id": "h2", "rev": 1, "region": "lh", "local": "2027-04-05T02:15:00",
     "fold": 1, "amount": "1.00"},
    {"id": "b1", "rev": 1, "region": "eu",
     "local": "2026-03-29T02:30:00", "amount": "9.99"},
    {"id": "b2", "rev": 1, "region": "eu",
     "local": "2026-10-25T02:15:00", "amount": "9.99"},
    {"id": "b3", "rev": 1, "region": "eu", "local": "2026-10-25T02:30:00",
     "fold": 0, "offset_min": 60, "amount": "9.99"},
    {"id": "b4", "rev": 1, "region": "lh",
     "local": "2026-10-05T02:15:00", "amount": "9.99"},
    {"id": "b5", "rev": 1, "region": "eu", "local": "2026-10-25T02:45:00",
     "fold": 7, "amount": "9.99"},
]}

# Out-of-order corrections: amount fix, void, stale rev replay, late fix.
BATCH2 = {"tz_version": "v1", "regions": REGIONS_V1, "events": [
    {"id": "e1", "rev": 2, "region": "east",
     "local": "2026-11-12T00:10:00", "amount": "2.35"},
    {"id": "f2", "rev": 2, "region": "eu",
     "local": "2026-10-25T02:30:00", "void": True},
    {"id": "e1", "rev": 1, "region": "east",
     "local": "2026-11-12T00:10:00", "amount": "1.35"},
    {"id": "s1", "rev": 2, "region": "eu",
     "local": "2026-03-29T01:59:59", "amount": "2.50"},
]}

BATCH3_CONFLICT = {"tz_version": "v1", "regions": REGIONS_V1, "events": [
    {"id": "e1", "rev": 2, "region": "east",
     "local": "2026-11-12T00:10:00", "amount": "9.99"},
]}

REGIONS_V2 = dict(REGIONS_V1)
REGIONS_V2["eu"] = {"initial_offset_min": 60, "transitions": [
    {"at_utc": "2026-03-29T01:00:00Z", "offset_min": 120},
    {"at_utc": "2026-10-25T02:00:00Z", "offset_min": 30}]}
BATCH4_TZSWITCH = {"tz_version": "v2", "regions": REGIONS_V2, "events": []}


def days_by_key(result):
    return {(d["region"], d["date"]): d for d in result["days"]}


def rejected_by_key(result):
    return {(r["id"], r["rev"]): r["code"] for r in result["rejected"]}


class BizdayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.batch = Path(self.tmp.name) / "batch.json"

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        proc = subprocess.run([sys.executable, str(BIZDAY), str(self.state),
                               *args], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def apply(self, batch):
        self.batch.write_text(json.dumps(batch))
        return self.cli("apply", str(self.batch))

    def inspect(self, version=None):
        args = ["inspect"] if version is None else ["inspect", "--version", str(version)]
        return self.cli(*args)

    def test_01_first_apply_days_events_rejects(self):
        res = self.apply(BATCH1)
        self.assertEqual(res["version"], 1)
        days = days_by_key(res)
        self.assertEqual(days[("east", "2026-11-12")], {
            "region": "east", "date": "2026-11-12", "count": 1, "minor": 135,
            "utc_start": "2026-11-11T16:00:00Z",
            "utc_end": "2026-11-12T16:00:00Z", "seconds": 86400})
        self.assertEqual(days[("eu", "2026-03-28")], {
            "region": "eu", "date": "2026-03-28", "count": 1, "minor": 100,
            "utc_start": "2026-03-27T23:00:00Z",
            "utc_end": "2026-03-28T23:00:00Z", "seconds": 86400})
        # 23-hour spring-forward day
        self.assertEqual(days[("eu", "2026-03-29")], {
            "region": "eu", "date": "2026-03-29", "count": 2, "minor": 500,
            "utc_start": "2026-03-28T23:00:00Z",
            "utc_end": "2026-03-29T22:00:00Z", "seconds": 82800})
        self.assertEqual(days[("eu", "2026-10-24")], {
            "region": "eu", "date": "2026-10-24", "count": 1, "minor": 75,
            "utc_start": "2026-10-23T22:00:00Z",
            "utc_end": "2026-10-24T22:00:00Z", "seconds": 86400})
        # 25-hour fall-back day; both fold occurrences book the same day
        self.assertEqual(days[("eu", "2026-10-25")], {
            "region": "eu", "date": "2026-10-25", "count": 3, "minor": 425,
            "utc_start": "2026-10-24T22:00:00Z",
            "utc_end": "2026-10-25T23:00:00Z", "seconds": 90000})
        # half-hour gap day (23.5h) and half-hour overlap day (24.5h)
        self.assertEqual(days[("lh", "2026-10-05")], {
            "region": "lh", "date": "2026-10-05", "count": 1, "minor": 400,
            "utc_start": "2026-10-04T13:30:00Z",
            "utc_end": "2026-10-05T13:00:00Z", "seconds": 84600})
        self.assertEqual(days[("lh", "2027-04-05")], {
            "region": "lh", "date": "2027-04-05", "count": 1, "minor": 100,
            "utc_start": "2027-04-04T13:00:00Z",
            "utc_end": "2027-04-05T13:30:00Z", "seconds": 88200})
        self.assertEqual(len(days), 7)

        events = {e["id"]: e for e in res["events"]}
        self.assertEqual(events["e1"]["utc"], "2026-11-11T16:10:00Z")
        self.assertEqual(events["s1"]["utc"], "2026-03-29T00:59:59Z")
        self.assertEqual(events["s2"]["utc"], "2026-03-29T01:00:00Z")
        self.assertEqual(events["m1"]["utc"], "2026-10-24T22:00:00Z")
        self.assertEqual(events["m2"]["utc"], "2026-10-24T21:59:59Z")
        self.assertEqual(events["m2"]["date"], "2026-10-24")
        self.assertEqual(events["f1"]["utc"], "2026-10-25T00:30:00Z")
        self.assertEqual(events["f2"]["utc"], "2026-10-25T01:30:00Z")
        self.assertEqual(events["h1"]["utc"], "2026-10-04T16:00:00Z")
        self.assertEqual(events["h2"]["utc"], "2027-04-04T15:45:00Z")
        self.assertEqual([e["id"] for e in res["events"]],
                         sorted(events))

        self.assertEqual(rejected_by_key(res), {
            ("b1", 1): "NONEXISTENT_LOCAL",
            ("b2", 1): "AMBIGUOUS_LOCAL",
            ("b3", 1): "OFFSET_MISMATCH",
            ("b4", 1): "NONEXISTENT_LOCAL",
            ("b5", 1): "AMBIGUOUS_LOCAL"})

    def test_02_replay_is_idempotent(self):
        first = self.apply(BATCH1)
        again = self.apply(BATCH1)
        self.assertEqual(again["version"], 1)
        self.assertEqual(again, first)

    def test_03_out_of_order_corrections_voids_and_versions(self):
        v1 = self.apply(BATCH1)
        v2 = self.apply(BATCH2)
        self.assertEqual(v2["version"], 2)
        days = days_by_key(v2)
        # correction replaces the amount on the same business day
        self.assertEqual(days[("east", "2026-11-12")]["minor"], 235)
        self.assertEqual(days[("east", "2026-11-12")]["count"], 1)
        # late correction still books its original (closed) business day
        self.assertEqual(days[("eu", "2026-03-29")]["minor"], 550)
        # void retracts from the original region and business date
        self.assertEqual(days[("eu", "2026-10-25")]["count"], 2)
        self.assertEqual(days[("eu", "2026-10-25")]["minor"], 175)
        events = {e["id"]: e for e in v2["events"]}
        self.assertNotIn("f2", events)
        self.assertEqual(events["e1"]["rev"], 2)
        self.assertEqual(events["s1"]["rev"], 2)
        self.assertEqual(events["e1"]["minor"], 235)
        # reconciliation diff vs the previously published version
        self.assertEqual(v2["diff"], [
            {"region": "east", "date": "2026-11-12",
             "count_delta": 0, "minor_delta": 100},
            {"region": "eu", "date": "2026-03-29",
             "count_delta": 0, "minor_delta": 50},
            {"region": "eu", "date": "2026-10-25",
             "count_delta": -1, "minor_delta": -250}])

        # replaying the same corrections twice changes nothing
        self.assertEqual(self.apply(BATCH2)["version"], 2)
        shuffled = dict(BATCH2, events=list(reversed(BATCH2["events"])))
        self.assertEqual(self.apply(shuffled)["version"], 2)

        # re-reading pre- and post-correction versions
        old = self.inspect(1)
        self.assertEqual(old, v1)
        old_days = days_by_key(old)
        self.assertEqual(old_days[("east", "2026-11-12")]["minor"], 135)
        self.assertEqual(old_days[("eu", "2026-10-25")]["minor"], 425)
        # cumulative delta between the two published versions equals diff
        for key, day in days_by_key(self.inspect(2)).items():
            before = old_days.get(key, {"count": 0, "minor": 0})
            delta = next((d for d in v2["diff"]
                          if (d["region"], d["date"]) == key), None)
            if delta is None:
                self.assertEqual((day["count"], day["minor"]),
                                 (before["count"], before["minor"]))
            else:
                self.assertEqual(day["count"] - before["count"],
                                 delta["count_delta"])
                self.assertEqual(day["minor"] - before["minor"],
                                 delta["minor_delta"])

    def test_04_conflicting_revision_rejected(self):
        self.apply(BATCH1)
        self.apply(BATCH2)
        res = self.apply(BATCH3_CONFLICT)
        self.assertEqual(res["version"], 3)
        self.assertEqual(rejected_by_key(res)[("e1", 2)],
                         "CONFLICTING_REVISION")
        events = {e["id"]: e for e in res["events"]}
        self.assertEqual(events["e1"]["minor"], 235)  # original stands

    def test_05_tz_switch_scoped_recompute(self):
        self.apply(BATCH1)
        self.apply(BATCH2)
        res = self.apply(BATCH4_TZSWITCH)
        self.assertEqual(res["version"], 3)
        self.assertEqual(res["tz_version"], "v2")
        # only eu rules changed; east/lh needed no recomputation
        self.assertEqual(res["affected_regions"], ["eu"])
        days = days_by_key(res)
        # 90-minute fall-back under v2: 25.5-hour day
        self.assertEqual(days[("eu", "2026-10-25")]["seconds"], 91800)
        self.assertEqual(days[("eu", "2026-10-25")]["utc_end"],
                         "2026-10-25T23:30:00Z")
        self.assertEqual(days[("eu", "2026-10-25")]["minor"], 175)
        # untouched regions keep their previously computed days
        self.assertEqual(days[("lh", "2026-10-05")]["seconds"], 84600)
        self.assertEqual(days[("east", "2026-11-12")]["minor"], 235)
        # counts and amounts unchanged -> empty reconciliation diff
        self.assertEqual(res["diff"], [])
        # repeating the switch commits nothing
        self.assertEqual(self.apply(BATCH4_TZSWITCH)["version"], 3)
        # historical versions remain readable after the switch
        self.assertEqual(days_by_key(self.inspect(1))[("eu", "2026-10-25")]["seconds"],
                         90000)

    def test_06_demo_still_works(self):
        demo = Path(__file__).with_name("demo.json")
        res = self.cli("apply", str(demo))
        self.assertEqual(res["version"], 1)
        self.assertEqual(days_by_key(res)[("east", "2026-11-12")]["minor"], 135)


if __name__ == "__main__":
    unittest.main(verbosity=2)

