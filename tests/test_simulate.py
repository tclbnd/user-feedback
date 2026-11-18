import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cluster import Cluster  # noqa: E402
from simulate import run  # noqa: E402


def ev(t, op, **kw):
    return {"t": t, "op": op, **kw}


def write(t, session, key, value):
    return ev(t, "write", session=session, key=key, value=value)


def read(t, session, key):
    return ev(t, "read", session=session, key=key)


class ClusterTests(unittest.TestCase):
    def test_replica_replay_lag(self):
        c = Cluster([{"name": "r1", "lag": 10}])
        c.commit(0, [("a", 1)])
        self.assertEqual(c.read("r1", "a", 9)["version"], 0)
        self.assertEqual(c.read("r1", "a", 10),
                         {"value": 1, "version": 1, "applied": 1})

    def test_visibility_chain_with_changing_lag(self):
        # a_i = max(a_(i-1), c_i + lag at c_i)
        c = Cluster([{"name": "r1", "lag": 100}])
        c.commit(0, [("k", 1)])       # visible at 100
        c.by_name["r1"].lag = 10
        c.commit(200, [("k", 2)])     # visible at max(100, 210) = 210
        c.by_name["r1"].lag = 5
        c.commit(220, [("k", 3)])     # visible at max(210, 225) = 225
        self.assertEqual(c.read("r1", "k", 150)["version"], 1)
        self.assertEqual(c.read("r1", "k", 209)["version"], 1)
        self.assertEqual(c.read("r1", "k", 210)["version"], 2)
        self.assertEqual(c.read("r1", "k", 224)["version"], 2)
        self.assertEqual(c.read("r1", "k", 225)["version"], 3)

    def test_primary_always_latest(self):
        c = Cluster([{"name": "r1", "lag": 100}])
        c.commit(0, [("a", 1)])
        c.commit(5, [("a", 2)])
        self.assertEqual(c.read("primary", "a", 6)["version"], 2)
        self.assertEqual(c.read("r1", "a", 6)["version"], 0)


class RoundRobinTests(unittest.TestCase):
    def test_reads_rotate_over_replicas(self):
        out = run({"replicas": [{"name": "r1", "lag": 1},
                                {"name": "r2", "lag": 1}],
                   "events": [write(0, "w", "a", 1)] +
                             [read(10 + n, "s", "a") for n in range(3)]})
        self.assertEqual([r["target"] for r in out["reads"]],
                         ["r1", "r2", "r1"])

    def test_cursor_advances_only_on_actual_service(self):
        # r1 is down when its turn comes; it is skipped and r2 serves.
        out = run({"replicas": [{"name": "r1", "lag": 1},
                                {"name": "r2", "lag": 1}],
                   "policy": {"on_stale": "primary", "probe_interval": 100},
                   "events": [write(0, "w", "a", 1),
                              ev(5, "down", replica="r1"),
                              read(10, "s", "a"),   # r1 rejects -> r2
                              read(11, "s", "a")]})  # cursor now at r1 again
        self.assertEqual([r["target"] for r in out["reads"]], ["r2", "r2"])
        self.assertEqual(out["reads"][0]["failed"], ["r1"])
        self.assertEqual(out["reads"][1]["failed"], [])


class ReadYourWritesTests(unittest.TestCase):
    def test_fresh_write_served_by_primary_when_replicas_stale(self):
        out = run({"replicas": [{"name": "r1", "lag": 100}],
                   "policy": {"on_stale": "primary"},
                   "events": [write(0, "alice", "a", 1),
                              read(5, "alice", "a")]})
        r = out["reads"][0]
        self.assertEqual(r["target"], "primary")
        self.assertEqual(r["served_at"], 5)
        self.assertEqual(r["value"], 1)
        self.assertEqual(r["version"], 1)
        self.assertEqual(r["failed"], [])

    def test_wait_until_replica_replays_session_commit(self):
        out = run({"replicas": [{"name": "r1", "lag": 100}],
                   "policy": {"on_stale": "wait", "max_wait": 200},
                   "events": [write(0, "alice", "a", 1),
                              read(5, "alice", "a")]})
        r = out["reads"][0]
        self.assertEqual((r["target"], r["served_at"], r["value"],
                          r["version"]), ("r1", 100, 1, 1))

    def test_wait_timeout_falls_back_to_primary_at_deadline(self):
        out = run({"replicas": [{"name": "r1", "lag": 100}],
                   "policy": {"on_stale": "wait", "max_wait": 50},
                   "events": [write(0, "alice", "a", 1),
                              read(5, "alice", "a")]})
        r = out["reads"][0]
        self.assertEqual((r["target"], r["served_at"], r["version"]),
                         ("primary", 55, 1))

    def test_wait_picks_earliest_ready_replica_round_robin(self):
        out = run({"replicas": [{"name": "r1", "lag": 80},
                                {"name": "r2", "lag": 30}],
                   "policy": {"on_stale": "wait", "max_wait": 200},
                   "events": [write(0, "alice", "a", 1),
                              read(5, "alice", "a")]})
        r = out["reads"][0]
        # r2 becomes eligible at t=30 first; at t=30 both are scanned from
        # cursor 0: r1 (applied 0) ineligible, r2 (applied 1) serves.
        self.assertEqual((r["target"], r["served_at"]), ("r2", 30))

    def test_session_without_write_uses_replica_immediately(self):
        out = run({"replicas": [{"name": "r1", "lag": 100}],
                   "policy": {"on_stale": "wait", "max_wait": 50},
                   "events": [write(0, "writer", "a", 1),
                              read(10, "reader", "a")]})
        r = out["reads"][0]
        self.assertEqual((r["target"], r["served_at"], r["version"]),
                         ("r1", 10, 0))
        self.assertIsNone(r["value"])


class MonotonicReadTests(unittest.TestCase):
    def test_session_never_reads_older_version_than_before(self):
        # r1 replays LSN 1 quickly but is then stuck there; r2 catches up
        # to every new LSN. The session writes both versions itself, so its
        # last_write requires LSN 2 and stale r1 (still at LSN 1) must be
        # skipped once the session has read LSN 2.
        out = run({"replicas": [{"name": "r1", "lag": 5},
                                {"name": "r2", "lag": 5}],
                   "policy": {"on_stale": "primary"},
                   "events": [write(0, "s", "a", 1),    # r1/r2 see LSN1 @5
                              ev(10, "set_lag", replica="r1", lag=100),
                              read(10, "s", "a"),      # r1 -> v1, last_read=1
                              write(20, "s", "a", 2),  # r1 LSN2 @120, r2 @25
                              read(22, "s", "a"),      # both below LSN2
                              read(25, "s", "a"),      # r2 -> v2, cursor->r1
                              read(26, "s", "a")]})    # r1 stale, skip -> r2
        targets = [r["target"] for r in out["reads"]]
        versions = [r["version"] for r in out["reads"]]
        self.assertEqual(targets, ["r1", "primary", "r2", "r2"])
        self.assertEqual(versions, [1, 2, 2, 2])
        self.assertTrue(all(versions[i] <= versions[i + 1]
                            for i in range(len(versions) - 1)))

    def test_fresh_session_may_read_old_data(self):
        # Two fresh sessions have no last_read/last_write, so an old replica
        # is acceptable for them even though another session saw newer data.
        out = run({"replicas": [{"name": "r1", "lag": 10},
                                {"name": "r2", "lag": 5}],
                   "policy": {"on_stale": "primary"},
                   "events": [write(0, "w", "a", 1),
                              write(20, "w", "a", 2),
                              read(25, "s1", "a"),  # r1 applied LSN1 only
                              read(26, "s2", "a")]})  # r2 applied both
        self.assertEqual(
            [(r["session"], r["target"], r["version"])
             for r in out["reads"]],
            [("s1", "r1", 1), ("s2", "r2", 2)])


class TransactionTests(unittest.TestCase):
    def test_all_txn_reads_and_writes_go_to_primary(self):
        out = run({"replicas": [{"name": "r1", "lag": 1000}],
                   "policy": {"on_stale": "wait", "max_wait": 5},
                   "events": [write(0, "w", "a", 1),
                              ev(10, "begin", session="alice"),
                              read(11, "alice", "a"),          # committed data
                              write(12, "alice", "a", 9),      # buffered
                              read(13, "alice", "a"),          # own buffered
                              read(14, "alice", "b"),          # unseen key
                              ev(15, "commit", session="alice"),
                              read(16, "alice", "a")]})        # post-commit RYW
        self.assertEqual([r["target"] for r in out["reads"]],
                         ["primary"] * 4)
        self.assertEqual(
            [(r["served_at"], r["value"], r["version"])
             for r in out["reads"]],
            [(11, 1, 1), (13, 9, None), (14, None, 0), (21, 9, 2)])
        # The final read still waits for the session's own commit (LSN 2);
        # max_wait=5 cannot cover lag 1000, so it falls back at t=16+5=21.
        self.assertEqual(out["reads"][3]["served_at"], 21)

    def test_txn_commit_records_new_lsn(self):
        out = run({"replicas": [{"name": "r1", "lag": 0}],
                   "policy": {"on_stale": "primary"},
                   "events": [write(0, "w", "a", 1),
                              ev(10, "begin", session="alice"),
                              write(11, "alice", "a", 2),
                              write(12, "alice", "a", 3),
                              ev(13, "commit", session="alice"),
                              read(14, "alice", "a")]})
        # Two buffered writes commit under one LSN (=2), immediately replayed.
        r = out["reads"][0]
        self.assertEqual(r["target"], "r1")
        self.assertEqual((r["value"], r["version"]), (3, 2))

    def test_empty_commit_commits_nothing(self):
        out = run({"replicas": [{"name": "r1", "lag": 0}],
                   "events": [ev(0, "begin", session="alice"),
                              ev(1, "commit", session="alice"),
                              read(2, "alice", "x")]})
        self.assertEqual(out["reads"][0]["target"], "r1")
        self.assertEqual(out["reads"][0]["version"], 0)


class FailoverTests(unittest.TestCase):
    def test_down_replica_removed_and_rerouted_same_time(self):
        out = run({"replicas": [{"name": "r1", "lag": 1},
                                {"name": "r2", "lag": 1},
                                {"name": "r3", "lag": 1}],
                   "policy": {"on_stale": "primary", "probe_interval": 100},
                   "events": [write(0, "w", "a", 1),
                              ev(5, "down", replica="r2"),
                              read(10, "s", "a"),
                              read(11, "s", "a"),
                              read(12, "s", "a"),
                              read(13, "s", "a")]})
        # Cursor r1,r2,r3,...: r1 serves; then r2 rejects at its turn and is
        # removed at the same time; r3 serves. After that r2 stays out.
        self.assertEqual([r["target"] for r in out["reads"]],
                         ["r1", "r3", "r1", "r3"])
        self.assertEqual([r["failed"] for r in out["reads"]],
                         [[], ["r2"], [], []])
        # Every read is served at its own arrival time, no clock drift.
        self.assertEqual([r["served_at"] for r in out["reads"]],
                         [10, 11, 12, 13])

    def test_replica_rejoins_after_probe_when_back_up(self):
        out = run({"replicas": [{"name": "r1", "lag": 1},
                                {"name": "r2", "lag": 1}],
                   "policy": {"on_stale": "primary", "probe_interval": 100},
                   "events": [write(0, "w", "a", 1),
                              ev(5, "down", replica="r1"),
                              read(10, "s", "a"),   # r1 rejects -> r2 serves
                              ev(50, "up", replica="r1"),
                              read(120, "s", "a")]})  # probe due: r1 serves
        self.assertEqual([r["target"] for r in out["reads"]], ["r2", "r1"])
        self.assertEqual(out["reads"][0]["failed"], ["r1"])
        self.assertEqual(out["reads"][1]["failed"], [])
        self.assertEqual([r["version"] for r in out["reads"]], [1, 1])

    def test_still_down_at_probe_gets_removed_again(self):
        out = run({"replicas": [{"name": "r1", "lag": 1},
                                {"name": "r2", "lag": 1}],
                   "policy": {"on_stale": "primary", "probe_interval": 100},
                   "events": [write(0, "w", "a", 1),
                              ev(5, "down", replica="r1"),
                              read(10, "s", "a"),    # r1 rejected, removed@10
                              read(120, "s", "a"),   # probe: rejects again@120
                              ev(150, "up", replica="r1"),
                              read(210, "s", "a"),   # 210 < 220: still out
                              read(221, "s", "a")]})  # probe due: back in
        self.assertEqual([r["target"] for r in out["reads"]],
                         ["r2", "r2", "r2", "r1"])
        self.assertEqual([r["failed"] for r in out["reads"]],
                         [["r1"], ["r1"], [], []])

    def test_all_replicas_down_primary_policy(self):
        out = run({"replicas": [{"name": "r1", "lag": 1},
                                {"name": "r2", "lag": 1}],
                   "policy": {"on_stale": "primary", "probe_interval": 100},
                   "events": [write(0, "w", "a", 1),
                              ev(5, "down", replica="r1"),
                              ev(6, "down", replica="r2"),
                              read(10, "s", "a")]})
        r = out["reads"][0]
        self.assertEqual(r["target"], "primary")
        self.assertEqual(r["failed"], ["r1", "r2"])
        self.assertEqual(r["version"], 1)

    def test_all_replicas_down_wait_policy_timeout_primary(self):
        out = run({"replicas": [{"name": "r1", "lag": 1}],
                   "policy": {"on_stale": "wait", "max_wait": 40,
                              "probe_interval": 100},
                   "events": [write(0, "w", "a", 1),
                              ev(5, "down", replica="r1"),
                              read(10, "s", "a")]})
        r = out["reads"][0]
        # probe at 110 exceeds deadline 50 -> primary at the deadline.
        self.assertEqual((r["target"], r["served_at"], r["failed"]),
                         ("primary", 50, ["r1"]))

    def test_removed_replica_probe_time_gates_wait(self):
        # Even though r1's LSN is ready at t=11, it was removed at 10 and
        # cannot serve before probe time 110; deadline 60 -> primary.
        out = run({"replicas": [{"name": "r1", "lag": 1}],
                   "policy": {"on_stale": "wait", "max_wait": 50,
                              "probe_interval": 100},
                   "events": [write(0, "w", "a", 1),
                              ev(5, "down", replica="r1"),
                              read(10, "s", "a")]})
        r = out["reads"][0]
        self.assertEqual((r["target"], r["served_at"]), ("primary", 60))
        self.assertEqual(r["failed"], ["r1"])


class ExampleScenarioTests(unittest.TestCase):
    def test_failover_example_full_trace(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import json
        with open(os.path.join(base, "examples", "failover.json")) as fh:
            scenario = json.load(fh)
        out = run(scenario)
        self.assertEqual(
            [(r["target"], r["served_at"], r["value"], r["version"],
              tuple(r["failed"])) for r in out["reads"]],
            [("replica-a", 110, 3, 1, ()),
             ("replica-c", 111, 3, 1, ("replica-b",)),
             ("replica-a", 112, 3, 1, ()),
             ("replica-c", 113, 3, 1, ()),
             ("replica-a", 420, 3, 1, ()),
             ("replica-b", 421, 3, 1, ()),
             ("primary", 501, 4, 2, ())])
        # RYW: alice's post-commit read requires LSN 2, replicas are at 1.
        self.assertEqual(out["reads"][-1]["session"], "alice")

    def test_profile_example_full_trace(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import json
        with open(os.path.join(base, "examples", "profile.json")) as fh:
            scenario = json.load(fh)
        out = run(scenario)
        trace = [(r["target"], r["served_at"], r["value"], r["version"])
                 for r in out["reads"]]
        self.assertEqual(trace, [
            ("replica-a", 500, "v1", 1),
            ("replica-a", 1040, "v2", 2),
            ("replica-a", 1060, "v2", 2),
            ("replica-b", 1061, "v1", 1),
            ("replica-a", 1062, "v2", 2),
            ("replica-a", 1063, "v2", 2),
            ("primary", 1101, "v2", 2),
            ("primary", 1103, "v3", None),
            ("replica-a", 1144, "v3", 3)])
        # Monotonic reads hold per session across the whole trace.
        per_session = {}
        for r in out["reads"]:
            v = r["version"]
            if v is not None:
                prev = per_session.get(r["session"], 0)
                self.assertGreaterEqual(v, prev)
                per_session[r["session"]] = max(prev, v)
        # Bob's own write-read view: v1 then never below v2.
        bob = [r["version"] for r in out["reads"]
               if r["session"] == "bob" and r["version"] is not None]
        self.assertEqual(bob, [1, 2, 2])


if __name__ == "__main__":
    unittest.main()
