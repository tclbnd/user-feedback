"""Differential and performance tests against an independent linear model.

Run with ``python3 reference_test.py``.
"""

import random
import unittest

from reservations import process


def reference(events):
    """A deliberately naive, independent implementation of the semantics."""
    ordered = sorted(events, key=lambda event: event["seq"])
    active = {}
    published = []
    published_ids = set()
    epochs = {}
    results = []

    for event in ordered:
        now = event["at"]
        for lease_id in [lid for lid, info in active.items() if info["expires"] <= now]:
            del active[lease_id]

        seq = event["seq"]
        op = event["op"]
        if op == "reserve":
            blockers = sorted(
                info["lease"]
                for info in active.values()
                if info["key"] == event["key"]
                and event["start"] < info["end"]
                and info["start"] < event["end"]
            )
            if blockers:
                results.append({"seq": seq, "status": "conflict", "blockers": blockers})
            else:
                epoch = epochs.get(event["key"], 0) + 1
                epochs[event["key"]] = epoch
                info = {
                    "key": event["key"],
                    "lease": seq,
                    "epoch": epoch,
                    "start": event["start"],
                    "end": event["end"],
                    "expires": event["at"] + event["ttl"],
                }
                active[seq] = info
                results.append(
                    {"seq": seq, "status": "reserved", "lease": seq, "epoch": epoch}
                )
        elif op in ("publish", "release"):
            info = active.get(event["lease"])
            if (
                info is None
                or info["key"] != event["key"]
                or info["epoch"] != event["epoch"]
            ):
                results.append({"seq": seq, "status": "stale"})
            elif op == "release":
                del active[event["lease"]]
                results.append({"seq": seq, "status": "released"})
            elif event["lease"] in published_ids:
                results.append({"seq": seq, "status": "already_published"})
            else:
                published_ids.add(event["lease"])
                published.append(dict(info))
                results.append({"seq": seq, "status": "published"})
        else:
            results.append({"seq": seq, "status": "tick"})

    def by_key(item):
        return item["key"], item["lease"]

    return {
        "results": results,
        "active": sorted(active.values(), key=by_key),
        "published": sorted(published, key=by_key),
        "epochs": epochs,
    }


def compare(test, events):
    expected = reference(events)
    actual = process(events)
    for field in ("results", "active", "published", "epochs"):
        test.assertEqual(actual[field], expected[field], "field %s differs" % field)
    return actual


def random_batch(rng, size=400, keys=5):
    events = []
    now = 0
    known = []  # (key, lease, epoch) successfully reserved but possibly stale
    seq = 0
    while len(events) < size:
        seq += 1
        now += rng.choice([0, 0, 0, 1, 1, 3, 10])
        roll = rng.random()
        if roll < 0.55 or not known:
            key = "k%d" % rng.randrange(keys)
            start = rng.randrange(0, 60)
            length = rng.randrange(1, 15)
            events.append(
                {
                    "seq": seq,
                    "at": now,
                    "op": "reserve",
                    "key": key,
                    "start": start,
                    "end": start + length,
                    "ttl": rng.randrange(1, 25),
                }
            )
            known.append((key, seq))
        elif roll < 0.72:
            key, lease = rng.choice(known)
            events.append(
                {
                    "seq": seq,
                    "at": now,
                    "op": "publish",
                    "key": key,
                    "lease": lease,
                    "epoch": rng.randrange(1, 4),
                }
            )
        elif roll < 0.88:
            key, lease = rng.choice(known)
            events.append(
                {
                    "seq": seq,
                    "at": now,
                    "op": "release",
                    "key": key,
                    "lease": lease,
                    "epoch": rng.randrange(1, 4),
                }
            )
        else:
            events.append({"seq": seq, "at": now, "op": "tick"})
    return events


class DifferentialTest(unittest.TestCase):
    def test_random_multi_key(self):
        for seed in range(40):
            with self.subTest(seed=seed):
                compare(self, random_batch(random.Random(seed)))

    def test_adjacent_intervals_do_not_conflict(self):
        events = [
            {"seq": 1, "at": 0, "op": "reserve", "key": "k", "start": 10, "end": 20, "ttl": 10},
            {"seq": 2, "at": 0, "op": "reserve", "key": "k", "start": 20, "end": 30, "ttl": 10},
            {"seq": 3, "at": 0, "op": "reserve", "key": "k", "start": 9, "end": 10, "ttl": 10},
        ]
        outcome = compare(self, events)
        self.assertEqual([r["status"] for r in outcome["results"]], ["reserved"] * 3)

    def test_overlap_reports_every_live_blocker(self):
        events = [
            {"seq": 1, "at": 0, "op": "reserve", "key": "k", "start": 0, "end": 10, "ttl": 10},
            {"seq": 2, "at": 0, "op": "reserve", "key": "k", "start": 10, "end": 20, "ttl": 10},
            {"seq": 3, "at": 0, "op": "reserve", "key": "k", "start": 20, "end": 30, "ttl": 10},
            {"seq": 4, "at": 0, "op": "reserve", "key": "k", "start": 5, "end": 25, "ttl": 10},
            {"seq": 5, "at": 0, "op": "reserve", "key": "other", "start": 5, "end": 25, "ttl": 10},
        ]
        outcome = compare(self, events)
        self.assertEqual(outcome["results"][3]["blockers"], [1, 2, 3])
        self.assertEqual(outcome["results"][4]["status"], "reserved")

    def test_expiry_frees_range_but_epoch_keeps_growing(self):
        events = [
            {"seq": 1, "at": 0, "op": "reserve", "key": "k", "start": 0, "end": 10, "ttl": 5},
            {"seq": 2, "at": 5, "op": "reserve", "key": "k", "start": 0, "end": 10, "ttl": 5},
            {"seq": 3, "at": 6, "op": "reserve", "key": "k", "start": 0, "end": 10, "ttl": 5},
            {"seq": 4, "at": 6, "op": "publish", "key": "k", "lease": 1, "epoch": 1},
        ]
        outcome = compare(self, events)
        statuses = [r["status"] for r in outcome["results"]]
        self.assertEqual(statuses, ["reserved", "reserved", "conflict", "stale"])
        self.assertEqual(outcome["epochs"], {"k": 2})
        self.assertEqual(outcome["results"][1]["epoch"], 2)

    def test_release_then_publish_and_duplicate_publish(self):
        events = [
            {"seq": 1, "at": 0, "op": "reserve", "key": "k", "start": 0, "end": 10, "ttl": 100},
            {"seq": 2, "at": 1, "op": "publish", "key": "k", "lease": 1, "epoch": 1},
            {"seq": 3, "at": 2, "op": "publish", "key": "k", "lease": 1, "epoch": 1},
            {"seq": 4, "at": 3, "op": "release", "key": "k", "lease": 1, "epoch": 2},
            {"seq": 5, "at": 4, "op": "release", "key": "k", "lease": 1, "epoch": 1},
            {"seq": 6, "at": 5, "op": "publish", "key": "other", "lease": 1, "epoch": 1},
            {"seq": 7, "at": 6, "op": "publish", "key": "k", "lease": 1, "epoch": 1},
        ]
        outcome = compare(self, events)
        self.assertEqual(
            [r["status"] for r in outcome["results"]],
            ["reserved", "published", "already_published", "stale", "released", "stale", "stale"],
        )
        self.assertEqual(len(outcome["published"]), 1)
        self.assertEqual(outcome["active"], [])

    def test_late_publish_after_expiry_is_stale(self):
        events = [
            {"seq": 1, "at": 0, "op": "reserve", "key": "k", "start": 0, "end": 10, "ttl": 5},
            {"seq": 2, "at": 4, "op": "tick"},
            {"seq": 3, "at": 5, "op": "publish", "key": "k", "lease": 1, "epoch": 1},
        ]
        outcome = compare(self, events)
        self.assertEqual(outcome["results"][2]["status"], "stale")

    def test_input_order_does_not_matter(self):
        events = random_batch(random.Random(7))
        expected = reference(events)
        for seed in range(5):
            shuffled = list(events)
            random.Random(seed).shuffle(shuffled)
            actual = process(shuffled)
            for field in ("results", "active", "published", "epochs"):
                self.assertEqual(actual[field], expected[field])


def performance_batch():
    events = []
    seq = 0
    now = 0
    # 10,000 disjoint live leases: [10*i, 10*i+5).
    for i in range(10000):
        seq += 1
        now += 0 if i % 7 else 0
        events.append(
            {
                "seq": seq,
                "at": now,
                "op": "reserve",
                "key": "k",
                "start": 10 * i,
                "end": 10 * i + 5,
                "ttl": 10 ** 9,
            }
        )
    # 5,000 probes, each strictly inside exactly one lease, plus boundary
    # touches and a tick (expiry scan) mixed in.
    for j in range(5000):
        seq += 1
        i = (j * 37) % 10000
        if j % 5 == 0:
            now += 1
        if j % 11 == 0:
            events.append({"seq": seq, "at": now, "op": "tick"})
            continue
        if j % 3 == 0:
            start, end = 10 * i + 5, 10 * i + 6  # adjacent, must not conflict
        else:
            start, end = 10 * i + 1, 10 * i + 2  # hits exactly lease i + 1
        events.append(
            {
                "seq": seq,
                "at": now,
                "op": "reserve",
                "key": "k",
                "start": start,
                "end": end,
                "ttl": 10 ** 9,
            }
        )
    return events


def performance_batch_with_expiry():
    """Same shape as performance_batch, but with leases expiring mid-stream."""
    events = []
    seq = 0
    now = 0
    for i in range(10000):
        seq += 1
        events.append(
            {
                "seq": seq,
                "at": now,
                "op": "reserve",
                "key": "k",
                "start": 10 * i,
                "end": 10 * i + 5,
                "ttl": 30 + (i % 50),
            }
        )
        if i % 100 == 0:
            now += 1
    for j in range(5000):
        seq += 1
        i = (j * 53) % 10000
        now += 1 if j % 4 == 0 else 0
        if j % 13 == 0:
            events.append({"seq": seq, "at": now, "op": "tick"})
            continue
        if j % 3 == 0:
            start, end = 10 * i + 5, 10 * i + 6
        else:
            start, end = 10 * i + 1, 10 * i + 2
        events.append(
            {
                "seq": seq,
                "at": now,
                "op": "reserve",
                "key": "k",
                "start": start,
                "end": end,
                "ttl": 1000000,
            }
        )
    return events


class PerformanceTest(unittest.TestCase):
    def test_large_workload_matches_reference_and_stays_bounded(self):
        events = performance_batch()
        expected = reference(events)
        actual = process(events)
        for field in ("results", "active", "published", "epochs"):
            self.assertEqual(actual[field], expected[field])
        self.assertLess(actual["stats"]["overlap_checks"], 500000)
        self.assertGreater(actual["stats"]["overlap_checks"], 0)

    def test_large_workload_with_expiry_matches_reference(self):
        events = performance_batch_with_expiry()
        expected = reference(events)
        actual = process(events)
        for field in ("results", "active", "published", "epochs"):
            self.assertEqual(actual[field], expected[field])
        statuses = {r["status"] for r in actual["results"]}
        self.assertIn("conflict", statuses)
        self.assertIn("reserved", statuses)
        self.assertLess(actual["stats"]["overlap_checks"], 500000)

    def test_large_workload_order_independent(self):
        events = performance_batch()
        baseline = process(events)
        shuffled = list(events)
        random.Random(123).shuffle(shuffled)
        other = process(shuffled)
        self.assertEqual(baseline["active"], other["active"])
        self.assertEqual(baseline["published"], other["published"])
        self.assertEqual(baseline["results"], other["results"])
        self.assertEqual(
            baseline["stats"]["overlap_checks"], other["stats"]["overlap_checks"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)