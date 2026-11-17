import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulate import run  # noqa: E402


def ev(t, op, **kw):
    return dict({"t": t, "op": op}, **kw)


def write(t, session, key, value):
    return ev(t, "write", session=session, key=key, value=value)


def read(t, session, key):
    return ev(t, "read", session=session, key=key)


def assert_reads(test, out, expected):
    # Assert every per-request field: target, served time, value, version,
    # and the replicas that rejected the read.
    actual = [(r["target"], r["served_at"], r["value"], r["version"],
               tuple(r["failed"])) for r in out["reads"]]
    test.assertEqual(actual, expected)


def assert_monotonic(test, out):
    # Within a session, successive reads must never expose an older version;
    # null-version (buffered txn) reads do not reset the floor.
    floor = {}
    for r in out["reads"]:
        seen = floor.setdefault(r["session"], 0)
        if r["version"] is not None:
            test.assertGreaterEqual(r["version"], seen,
                                    "non-monotonic read: %r" % r)
            floor[r["session"]] = max(seen, r["version"])


class ReadYourWritesTests(unittest.TestCase):
    def test_wait_until_a_replica_replays_the_commit(self):
        scenario = {
            "replicas": [{"name": "r1", "lag": 30}, {"name": "r2", "lag": 80}],
            "policy": {"on_stale": "wait", "max_wait": 50, "probe_interval": 100},
            "events": [
                write(0, "s1", "a", 1),       # LSN1: r1@30, r2@80
                read(5, "s1", "a"),            # waits -> r1 at 30
                read(40, "s1", "a"),           # r1 already eligible
                read(85, "s1", "a"),           # round-robin reaches r2
            ],
        }
        out = run(scenario)
        assert_reads(self, out, [
            ("r1", 30, 1, 1, ()),
            ("r1", 40, 1, 1, ()),
            ("r2", 85, 1, 1, ()),
        ])
        assert_monotonic(self, out)

    def test_primary_fallback_is_immediate(self):
        scenario = {
            "replicas": [{"name": "r1", "lag": 30}, {"name": "r2", "lag": 80}],
            "policy": {"on_stale": "primary", "probe_interval": 100},
            "events": [
                write(0, "s1", "a", 7),
                read(5, "s1", "a"),            # no replica ready -> primary now
                read(35, "s1", "a"),           # r1 has replayed LSN1
            ],
        }
        out = run(scenario)
        assert_reads(self, out, [
            ("primary", 5, 7, 1, ()),
            ("r1", 35, 7, 1, ()),
        ])

    def test_wait_times_out_at_deadline_then_primary(self):
        scenario = {
            "replicas": [{"name": "r1", "lag": 30}],
            "policy": {"on_stale": "wait", "max_wait": 20},
            "events": [
                write(0, "s1", "a", 1),        # visible on r1 at 30
                read(5, "s1", "a"),            # deadline 25 < 30 -> primary
            ],
        }
        out = run(scenario)
        assert_reads(self, out, [("primary", 25, 1, 1, ())])


class MonotonicReadsTests(unittest.TestCase):
    def test_session_never_reads_older_than_its_last_read(self):
        # r1 keeps pace; r2 is far behind and must never serve this session
        # once it has seen LSN2.
        scenario = {
            "replicas": [{"name": "r1", "lag": 10}, {"name": "r2", "lag": 100}],
            "policy": {"on_stale": "primary", "probe_interval": 1000},
            "events": [
                write(0, "alice", "a", 1),     # LSN1: r1@10, r2@100
                write(20, "alice", "a", 2),    # LSN2: r1@30, r2@120
                read(30, "bob", "a"),          # r1 serves LSN2
                read(31, "bob", "a"),          # r2 ineligible; r1 again
                ev(32, "down", replica="r1"),
                read(33, "bob", "a"),          # r1 rejects, r2 stale -> primary
                read(34, "bob", "a"),          # r1 still removed -> primary
            ],
        }
        out = run(scenario)
        assert_reads(self, out, [
            ("r1", 30, 2, 2, ()),
            ("r1", 31, 2, 2, ()),
            ("primary", 33, 2, 2, ("r1",)),
            ("primary", 34, 2, 2, ()),
        ])
        assert_monotonic(self, out)

    def test_last_read_floor_holds_without_any_session_write(self):
        # bob never writes; his floor comes solely from previous reads.
        scenario = {
            "replicas": [{"name": "r1", "lag": 5}, {"name": "r2", "lag": 50}],
            "policy": {"on_stale": "primary"},
            "events": [
                write(0, "alice", "k", "x"),   # LSN1: r1@5, r2@50
                read(10, "bob", "k"),          # r1 LSN1 -> floor 1
                read(11, "bob", "k"),          # r2 at LSN0 ineligible -> r1
            ],
        }
        out = run(scenario)
        assert_reads(self, out, [
            ("r1", 10, "x", 1, ()),
            ("r1", 11, "x", 1, ()),
        ])


class TransactionTests(unittest.TestCase):
    def test_every_read_inside_transaction_hits_primary(self):
        scenario = {
            "replicas": [{"name": "r1", "lag": 100}],
            "policy": {"on_stale": "wait", "max_wait": 5},
            "events": [
                write(0, "alice", "a", 1),     # LSN1
                ev(10, "begin", session="alice"),
                read(11, "alice", "a"),        # primary: committed value
                write(12, "alice", "a", 99),   # buffered, no LSN yet
                read(13, "alice", "a"),        # primary: buffered value, null
                ev(14, "commit", session="alice"),  # LSN2
                # replica stale: waits to deadline 20, then primary fallback
                read(15, "alice", "a"),
            ],
        }
        out = run(scenario)
        assert_reads(self, out, [
            ("primary", 11, 1, 1, ()),
            ("primary", 13, 99, None, ()),
            ("primary", 20, 99, 2, ()),
        ])
        assert_monotonic(self, out)

    def test_buffered_write_is_not_visible_on_replicas_before_commit(self):
        scenario = {
            "replicas": [{"name": "r1", "lag": 0}],
            "policy": {"on_stale": "primary"},
            "events": [
                ev(0, "begin", session="s1"),
                write(1, "s1", "b", 5),
                read(2, "s1", "b"),            # buffered -> null version
                ev(3, "commit", session="s1"),
                read(4, "s1", "b"),            # now committed LSN1
            ],
        }
        out = run(scenario)
        assert_reads(self, out, [
            ("primary", 2, 5, None, ()),
            ("r1", 4, 5, 1, ()),
        ])


class ReplicaFailoverTests(unittest.TestCase):
    def _scenario(self, policy):
        return {
            "replicas": [{"name": "r1", "lag": 2},
                         {"name": "r2", "lag": 2},
                         {"name": "r3", "lag": 2}],
            "policy": policy,
            "events": [
                write(0, "s", "a", 1),
                ev(5, "down", replica="r2"),
                read(10, "s", "a"),            # r1
                read(11, "s", "a"),            # r2 rejects -> r3
                read(12, "s", "a"),            # r2 removed -> r1
                read(13, "s", "a"),            # r2 removed -> r3
                read(120, "s", "a"),           # probe window over, still down;
                                               # cursor lands on r1 first
                read(121, "s", "a"),           # r2 retried, rejects again
                ev(130, "up", replica="r2"),
                read(230, "s", "a"),           # r2 healthy again; cursor -> r1
                read(231, "s", "a"),           # r2 serves, removal cleared
            ],
        }

    def test_removal_and_reprobe_primary_policy(self):
        out = run(self._scenario(
            {"on_stale": "primary", "probe_interval": 100}))
        assert_reads(self, out, [
            ("r1", 10, 1, 1, ()),
            ("r3", 11, 1, 1, ("r2",)),
            ("r1", 12, 1, 1, ()),
            ("r3", 13, 1, 1, ()),
            ("r1", 120, 1, 1, ()),
            ("r3", 121, 1, 1, ("r2",)),
            ("r1", 230, 1, 1, ()),
            ("r2", 231, 1, 1, ()),
        ])
        assert_monotonic(self, out)

    def test_rejection_reroutes_immediately_under_wait_policy(self):
        out = run(self._scenario(
            {"on_stale": "wait", "max_wait": 500, "probe_interval": 100}))
        # A rejected read is re-routed at the same instant; no clock spent.
        actual = [(r["target"], r["served_at"], tuple(r["failed"]))
                  for r in out["reads"]]
        self.assertEqual(actual[1], ("r3", 11, ("r2",)))
        self.assertEqual(actual[5], ("r3", 121, ("r2",)))


class LagAdjustmentTests(unittest.TestCase):
    def test_set_lag_affects_only_later_commits(self):
        scenario = {
            "replicas": [{"name": "r1", "lag": 100}],
            "policy": {"on_stale": "wait", "max_wait": 200},
            "events": [
                write(0, "s", "a", 1),         # LSN1 visible at 100
                ev(10, "set_lag", replica="r1", lag=10),
                write(20, "s", "a", 2),        # LSN2: max(100, 30) = 100
                read(30, "s", "a"),            # waits until 100, gets LSN2
            ],
        }
        out = run(scenario)
        assert_reads(self, out, [("r1", 100, 2, 2, ())])


class RoundRobinTests(unittest.TestCase):
    def test_cursor_skips_ineligible_replica(self):
        scenario = {
            "replicas": [{"name": "r1", "lag": 100}, {"name": "r2", "lag": 10}],
            "policy": {"on_stale": "wait", "max_wait": 200},
            "events": [
                write(0, "s", "a", 1),         # r1@100, r2@10
                read(5, "s", "a"),             # earliest ready: r2 at 10
                read(20, "s", "a"),            # r1 still stale -> r2 again
            ],
        }
        out = run(scenario)
        assert_reads(self, out, [
            ("r2", 10, 1, 1, ()),
            ("r2", 20, 1, 1, ()),
        ])


if __name__ == "__main__":
    unittest.main()
