#!/usr/bin/env python3
"""Invariant / acceptance tests for fair.py (stdlib unittest)."""

import unittest

import fair


def sim(jobs, horizon=60, quantum=1):
    return {"jobs": jobs, "horizon": horizon, "quantum": quantum}


def bill_segments(events, horizon, job_ids):
    """Independent reference replay: explicit segment intersection."""
    segs = {}
    open_at = {}
    done = set()
    for ev in events:
        j, k, t = ev["job"], ev["kind"], float(ev["t"])
        if k in ("start", "resume"):
            if j in done or j in open_at:
                continue
            open_at[j] = t
        elif k == "pause":
            if j in open_at:
                segs.setdefault(j, []).append((open_at.pop(j), t))
        elif k == "finish":
            if j in done:
                continue
            if j in open_at:
                segs.setdefault(j, []).append((open_at.pop(j), t))
            done.add(j)
    for j, s in open_at.items():
        segs.setdefault(j, []).append((s, horizon))
    return {j: sum(b - a for a, b in segs.get(j, [])) for j in job_ids}


def surface(res):
    """Everything that chunking must not change."""
    return {k: res[k] for k in
            ("events", "bills", "preempts", "finish_order", "util")}


class PreemptionTest(unittest.TestCase):
    def setUp(self):
        self.s = sim([
            {"id": "low", "w": 1, "cost": 10, "arrive": 0},
            {"id": "high", "w": 4, "cost": 3, "arrive": 3},
        ], horizon=30)
        self.res = fair.simulate(self.s)

    def test_events_use_segment_vocabulary(self):
        kinds = [e["kind"] for e in self.res["events"]]
        self.assertEqual(set(kinds), {"start", "resume", "pause", "finish"})

    def test_low_is_paused_and_resumed(self):
        low_kinds = [e["kind"] for e in self.res["events"]
                     if e["job"] == "low"]
        self.assertIn("pause", low_kinds)
        self.assertIn("resume", low_kinds)
        self.assertEqual(self.res["preempts"]["low"], 1)

    def test_effective_time_matches_reference(self):
        ids = ["low", "high"]
        ref = bill_segments(self.res["events"], 30, ids)
        for jid in ids:
            self.assertEqual(self.res["bills"][jid], ref[jid],
                             "bill mismatch for %s" % jid)

    def test_no_double_billing_span(self):
        # Naive accounting billed the whole 0..3 span on preemption and then
        # billed the job again from its resume point.
        self.assertEqual(self.res["bills"]["low"], 10)
        self.assertEqual(self.res["bills"]["high"], 3)

    def test_completed_job_billed_exactly_its_cost(self):
        for job in self.s["jobs"]:
            if job["id"] in self.res["finish_order"]:
                self.assertEqual(self.res["bills"][job["id"]], job["cost"])

    def test_util_is_busy_over_horizon(self):
        busy = sum(self.res["bills"].values())
        self.assertAlmostEqual(self.res["util"], busy / 30.0, places=9)

    def test_checkpoint_resume_continues_work(self):
        # 3s served before the first preemption; the runner retains its
        # checkpoint and, after resume, serves only its remaining 7s.  Later
        # pauses come from plain fair-share rotation, not preemption, so they
        # do not increment preempts.
        low = self.res["details"]["low"]
        self.assertEqual(low["checkpoint"], 10)
        self.assertEqual(low["effective_sec"], 10)
        self.assertEqual(self.res["preempts"]["low"], 1)
        resumes = [e["t"] for e in self.res["events"]
                   if e["job"] == "low" and e["kind"] == "resume"]
        pauses = [e["t"] for e in self.res["events"]
                  if e["job"] == "low" and e["kind"] == "pause"]
        fin = [e["t"] for e in self.res["events"]
               if e["job"] == "low" and e["kind"] == "finish"]
        self.assertEqual(len(resumes), len(pauses))
        # Total served time is exactly the cost even though it is split.
        served = 0.0
        prev = [e["t"] for e in self.res["events"]
                if e["job"] == "low" and e["kind"] == "start"][0]
        for e in self.res["events"]:
            if e["job"] != "low":
                continue
            if e["kind"] in ("pause", "finish"):
                served += e["t"] - prev
            elif e["kind"] == "resume":
                prev = e["t"]
        self.assertAlmostEqual(served, 10.0, places=6)


class ReplayTest(unittest.TestCase):
    def test_replay_of_scheduler_events_round_trips(self):
        s = sim([{"id": "a", "w": 1, "cost": 7, "arrive": 0},
                 {"id": "b", "w": 5, "cost": 4, "arrive": 2},
                 {"id": "c", "w": 2, "cost": 9, "arrive": 5}], horizon=40)
        res = fair.simulate(s)
        again = fair.replay(res["events"], horizon=40,
                            jobs=[j["id"] for j in s["jobs"]])
        self.assertEqual(again["bills"], res["bills"])
        self.assertEqual(again["finish_order"], res["finish_order"])

    def test_duplicate_finish_and_rollback_not_double_counted(self):
        events = [
            {"t": 0, "job": "x", "kind": "start"},
            {"t": 2, "job": "x", "kind": "pause"},
            {"t": 5, "job": "x", "kind": "resume"},
            {"t": 7, "job": "x", "kind": "finish"},
            {"t": 7, "job": "x", "kind": "finish"},
            {"t": 8, "job": "x", "kind": "resume"},
            {"t": 9, "job": "x", "kind": "finish"},
        ]
        out = fair.replay(events, horizon=20, jobs=["x"])
        self.assertEqual(out["bills"]["x"], 4)
        self.assertEqual(out["finish_order"], ["x"])

    def test_chunked_replay_is_identical(self):
        s = sim([{"id": "a", "w": 1, "cost": 9, "arrive": 0},
                 {"id": "b", "w": 3, "cost": 9, "arrive": 0}], horizon=40)
        res = fair.simulate(s)
        ids = [j["id"] for j in s["jobs"]]
        base = fair.replay(res["events"], horizon=40, jobs=ids)
        for size in (1, 2, 3, 7, 100):
            chunked = fair.replay_chunked(res["events"], horizon=40, jobs=ids,
                                          chunk_size=size)
            self.assertEqual(chunked["bills"], base["bills"])
            self.assertEqual(chunked["finish_order"], base["finish_order"])

    def test_scheduler_events_chunked_identically(self):
        s = sim([{"id": "a", "w": 1, "cost": 11, "arrive": 0},
                 {"id": "b", "w": 4, "cost": 5, "arrive": 3},
                 {"id": "c", "w": 2, "cost": 8, "arrive": 6}], horizon=35)
        base = surface(fair.simulate(s))
        for size in (1, 2, 5, 1000):
            self.assertEqual(surface(fair.simulate_chunked(s, chunk_size=size)),
                             base, "chunk=%s diverged" % size)

    def test_chunked_arrival_stream_same_decisions(self):
        jobs = [{"id": "a", "w": 1, "cost": 12, "arrive": 0},
                {"id": "b", "w": 4, "cost": 6, "arrive": 2},
                {"id": "c", "w": 6, "cost": 4, "arrive": 7}]
        flat = surface(fair.simulate(sim(jobs, horizon=30)))
        chunked_sim = {"horizon": 30, "quantum": 1,
                       "chunks": [{"jobs": jobs[:1]},
                                  {"jobs": jobs[1:2]},
                                  {"jobs": jobs[2:]}]}
        self.assertEqual(surface(fair.simulate(chunked_sim)), flat)


class StarvationTest(unittest.TestCase):
    def _max_gap(self, events, horizon, job_id):
        ons = sorted(float(e["t"]) for e in events
                     if e["job"] == job_id and e["kind"] in ("start", "resume"))
        offs = sorted(float(e["t"]) for e in events
                      if e["job"] == job_id and e["kind"] == "pause")
        segs = []
        for k in range(len(ons)):
            end = offs[k] if k < len(offs) else horizon
            segs.append((ons[k], end))
        if not segs:
            return horizon
        gaps = [segs[0][0]]
        for (a1, b1), (a2, _) in zip(segs, segs[1:]):
            gaps.append(a2 - b1)
        gaps.append(horizon - segs[-1][1])
        return max(gaps)

    def test_low_weight_wait_is_bounded_under_burst(self):
        jobs = [{"id": "low", "w": 1, "cost": 12, "arrive": 0}]
        for t in range(1, 60):
            jobs.append({"id": "burst%02d" % t, "w": 8, "cost": 1,
                         "arrive": t})
        res = fair.simulate(sim(jobs, horizon=60))
        gap = self._max_gap(res["events"], 60, "low")
        self.assertLessEqual(gap, fair.STARVATION_BOUND + 1.0,
                             "low-weight job starved for %.1fs" % gap)
        self.assertGreater(res["bills"]["low"], 0)

    def test_repeated_preemption_grants_immunity(self):
        # Heavy arrivals every 2s: the victim is preempted until the counter
        # passes PREEMPT_LIMIT, then earns a quiet window and must not be
        # preempted again during it.
        jobs = [{"id": "victim", "w": 1, "cost": 40, "arrive": 0}]
        for t in range(2, 42, 2):
            jobs.append({"id": "h%02d" % t, "w": 6, "cost": 1, "arrive": t})
        res = fair.simulate(sim(jobs, horizon=42))
        preempt_times = [d["t"] for d in res["decisions"]
                         if d.get("victim") == "victim"]
        self.assertGreater(len(preempt_times), fair.PREEMPT_LIMIT)
        # After the cap is exceeded the victim is left alone for ~IMMUNITY.
        cap = preempt_times[fair.PREEMPT_LIMIT]
        later = [t for t in preempt_times if t > cap + 1]
        if later:
            self.assertGreaterEqual(min(later) - cap,
                                    fair.IMMUNITY_SECONDS - 2,
                                    "immunity window was ignored")


class ShareTest(unittest.TestCase):
    def test_three_weight_configs_within_five_percent(self):
        for weights in ([1, 2, 4], [1, 1, 8], [2, 3, 5]):
            jobs = [{"id": "j%d" % i, "w": w, "cost": 1000, "arrive": 0}
                    for i, w in enumerate(weights)]
            res = fair.simulate(sim(jobs, horizon=60))
            total = sum(res["bills"].values())
            self.assertAlmostEqual(res["util"], 1.0, places=6)
            tw = float(sum(weights))
            for i, w in enumerate(weights):
                got = res["bills"]["j%d" % i] / total
                want = w / tw
                self.assertLessEqual(abs(got - want), 0.05 * want,
                                     "weights=%s job=%d got=%.4f want=%.4f"
                                     % (weights, i, got, want))

    def test_share_util_matches_actual_bill_ratios(self):
        jobs = [{"id": "a", "w": 1, "cost": 1000, "arrive": 0},
                {"id": "b", "w": 2, "cost": 1000, "arrive": 0},
                {"id": "c", "w": 5, "cost": 1000, "arrive": 0}]
        res = fair.simulate(sim(jobs, horizon=60))
        busy = sum(res["bills"].values())
        for jid in ("a", "b", "c"):
            self.assertAlmostEqual(res["share_util"][jid],
                                   res["bills"][jid] / busy, places=6)

    def test_decisions_report_weights_and_shares(self):
        jobs = [{"id": "a", "w": 1, "cost": 100, "arrive": 0},
                {"id": "b", "w": 3, "cost": 100, "arrive": 0}]
        res = fair.simulate(sim(jobs, horizon=20))
        share_dec = [d for d in res["decisions"] if d["mode"] == "share"]
        self.assertTrue(share_dec)
        for d in share_dec:
            self.assertAlmostEqual(sum(d["share"].values()), 1.0, places=6)
            self.assertEqual(d["share"]["b"], 0.75)


class RobustnessTest(unittest.TestCase):
    def test_no_booked_time_overlaps(self):
        jobs = [{"id": "a", "w": 1, "cost": 9, "arrive": 0},
                {"id": "b", "w": 5, "cost": 4, "arrive": 1},
                {"id": "c", "w": 2, "cost": 7, "arrive": 2},
                {"id": "d", "w": 8, "cost": 3, "arrive": 2},
                {"id": "e", "w": 1, "cost": 6, "arrive": 4}]
        res = fair.simulate(sim(jobs, horizon=30))
        boundaries = sorted({0.0, 30.0} |
                            {e["t"] for e in res["events"]})
        for a, b in zip(boundaries, boundaries[1:]):
            if b - a < 1e-9:
                continue
            mid = (a + b) / 2
            open_jobs = set()
            done = set()
            for ev in res["events"]:
                if ev["t"] > mid:
                    break
                if ev["kind"] in ("start", "resume"):
                    if ev["job"] not in done:
                        open_jobs.add(ev["job"])
                else:
                    open_jobs.discard(ev["job"])
                    if ev["kind"] == "finish":
                        done.add(ev["job"])
            self.assertLessEqual(len(open_jobs), 1,
                                 "two jobs billed at once over %.3f: %s"
                                 % (mid, open_jobs))

    def test_equal_weight_heavy_arrivals_terminate(self):
        # Regression: near-identical virtual times used to produce an
        # unbounded series of micro-epochs.
        jobs = [{"id": "j%02d" % i, "w": 4, "cost": 13, "arrive": i * 2}
                for i in range(20)]
        res = fair.simulate(sim(jobs, horizon=60))
        self.assertLess(sum(res["bills"].values()), 60 + 1e-6)

    def test_empty_and_degenerate_inputs(self):
        self.assertEqual(fair.simulate({"jobs": [], "horizon": 10})["bills"],
                         {})
        res = fair.simulate({"jobs": [{"id": "z", "w": 1, "cost": 0,
                                       "arrive": 0}], "horizon": 5})
        self.assertEqual(res["bills"]["z"], 0)
        self.assertEqual(res["finish_order"], ["z"])
        res = fair.simulate({"jobs": [{"id": "z", "w": 1, "cost": 5,
                                       "arrive": 0}], "horizon": 0})
        self.assertEqual(res["bills"]["z"], 0)

    def test_whole_burst_finishes_within_horizon(self):
        jobs = [{"id": "a", "w": 3, "cost": 5, "arrive": 0},
                {"id": "b", "w": 7, "cost": 5, "arrive": 0},
                {"id": "c", "w": 1, "cost": 5, "arrive": 0}]
        res = fair.simulate(sim(jobs, horizon=20))
        self.assertEqual(sorted(res["finish_order"]), ["a", "b", "c"])
        for d in res["details"].values():
            self.assertAlmostEqual(d["effective_sec"], d["cost"], places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)

