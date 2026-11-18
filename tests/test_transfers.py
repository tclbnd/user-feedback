import unittest

from cluster import run
from xshard.coordinator import Coordinator
from xshard.participant import LockConflict, Participant, Down

BASE = {"shards": {"A": {"alice": 100}, "B": {"bob": 100}}, "placement": {"alice": "A", "bob": "B"}}


def transfer(crash=None):
    return {"op": "transfer", "tx": "t1", "from": "alice", "to": "bob", "amount": 30, "crash": crash}


def recovered(crash):
    """Run a transfer under a crash injection, then fully recover once."""
    steps = [transfer(crash), {"op": "restart_coordinator"}, {"op": "recover"}]
    return run(dict(BASE, steps=steps))


class TransferTests(unittest.TestCase):
    def test_cross_shard_transfer_commits(self):
        out = run(dict(BASE, steps=[{"op": "transfer", "tx": "t1", "from": "alice", "to": "bob", "amount": 30}]))
        self.assertEqual(out["outcomes"]["t1"], {"A": "committed", "B": "committed"})
        self.assertEqual(out["report"]["total"], 200)
        self.assertEqual(out["locks"], {"A": {}, "B": {}})

    def test_local_write_blocked_by_lock(self):
        shard = Participant("A", {"alice": 10})
        self.assertTrue(shard.prepare("t1", {"alice": -5}))
        with self.assertRaises(LockConflict):
            shard.write("alice", 1)
        shard.commit("t1")
        shard.write("alice", 1)
        self.assertEqual(shard.data["alice"], 6)


class TwoPhaseCommitRecoveryTests(unittest.TestCase):
    def assert_converged(self, out, outcome, total=200):
        """Every shard reaches the same terminal state and no lock remains."""
        self.assertEqual(set(out["outcomes"]["t1"].values()), {outcome})
        self.assertEqual(out["locks"], {"A": {}, "B": {}})
        self.assertEqual(out["report"]["total"], total)

    def test_no_crash_commits_everywhere(self):
        self.assert_converged(run(dict(BASE, steps=[transfer()])), "committed")

    def test_crash_after_prepare_aborts_everywhere_and_releases_locks(self):
        out = recovered("after_prepare")
        self.assert_converged(out, "aborted")
        # A follow-up local write must be accepted on every formerly locked row.
        follow = run(dict(BASE, steps=[
            transfer("after_prepare"),
            {"op": "restart_coordinator"},
            {"op": "recover"},
            {"op": "write", "shard": "A", "key": "alice", "delta": 5},
            {"op": "write", "shard": "B", "key": "bob", "delta": 7},
        ]))
        self.assertTrue(all(e.get("ok") for e in follow["events"] if "write" in e))
        self.assertEqual(follow["report"]["total"], 212)

    def test_crash_after_first_decision_commits_everywhere(self):
        # The durable decision must drive the shard that missed the message
        # to the same outcome as the shard that already committed.
        out = recovered("after_first_decision")
        self.assert_converged(out, "committed")
        self.assertEqual(out["report"]["rows"], [
            {"shard": "A", "account": "alice", "balance": 70},
            {"shard": "B", "account": "bob", "balance": 130},
        ])

    def test_participant_crash_after_prepare_rebuilds_locks_and_converges(self):
        for crashed in ("A", "B"):
            out = run(dict(BASE, steps=[
                transfer(f"participant_after_prepare:{crashed}"),
                {"op": "restart_shard", "shard": crashed},
                {"op": "recover"},
            ]))
            self.assert_converged(out, "committed")

    def test_coordinator_crashing_twice_before_decision_converges(self):
        out = run(dict(BASE, steps=[
            transfer("after_prepare"),
            {"op": "crash_shard", "shard": "B"},
            {"op": "restart_coordinator"},
            {"op": "recover"},
            {"op": "restart_coordinator"},
            {"op": "restart_shard", "shard": "B"},
            {"op": "recover"},
            {"op": "write", "shard": "A", "key": "alice", "delta": 5},
            {"op": "write", "shard": "B", "key": "bob", "delta": 7},
        ]))
        self.assert_converged(out, "aborted", total=212)

    def test_recovery_is_idempotent_across_repeated_passes(self):
        out = run(dict(BASE, steps=[
            transfer("after_first_decision"),
            {"op": "restart_coordinator"},
            {"op": "recover"},
            {"op": "restart_coordinator"},
            {"op": "recover"},
            {"op": "restart_coordinator"},
            {"op": "recover"},
        ]))
        self.assert_converged(out, "committed")

    def test_shard_down_during_prepare_aborts_the_whole_transaction(self):
        out = run(dict(BASE, steps=[
            {"op": "crash_shard", "shard": "B"},
            transfer(),
            {"op": "restart_shard", "shard": "B"},
            {"op": "recover"},
        ]))
        outcomes = out["outcomes"]["t1"]
        # B never received the prepare (it was down): it has no record and
        # holds no lock.  A voted yes and must be driven to abort, so the
        # transaction cannot commit on any shard that touched it.
        self.assertEqual(outcomes["A"], "aborted")
        self.assertEqual(outcomes["B"], "unknown")
        self.assertEqual(out["locks"], {"A": {}, "B": {}})
        self.assertEqual(out["report"]["total"], 200)


class ParticipantRestartTests(unittest.TestCase):
    def test_restart_rebuilds_locks_of_prepared_transaction(self):
        shard = Participant("A", {"alice": 100})
        self.assertTrue(shard.prepare("t1", {"alice": -30}))
        shard.crash()
        shard.restart()
        # The in-doubt transaction survives and keeps holding its row lock.
        self.assertEqual(shard.outcome("t1"), "prepared")
        self.assertEqual(shard.locks, {"alice": "t1"})
        with self.assertRaises(LockConflict):
            shard.write("alice", 1)

    def test_restart_after_commit_keeps_effect_and_drops_lock(self):
        shard = Participant("A", {"alice": 100})
        shard.prepare("t1", {"alice": -30})
        shard.commit("t1")
        shard.crash()
        shard.restart()
        self.assertEqual(shard.data["alice"], 70)
        self.assertEqual(shard.locks, {})
        self.assertEqual(shard.outcome("t1"), "committed")
        shard.write("alice", 5)
        self.assertEqual(shard.data["alice"], 75)

    def test_restart_after_abort_drops_lock(self):
        shard = Participant("A", {"alice": 100})
        shard.prepare("t1", {"alice": -30})
        shard.abort("t1")
        shard.crash()
        shard.restart()
        self.assertEqual(shard.data["alice"], 100)
        self.assertEqual(shard.locks, {})
        self.assertEqual(shard.outcome("t1"), "aborted")

    def test_duplicate_decision_is_applied_once(self):
        shard = Participant("A", {"alice": 100})
        shard.prepare("t1", {"alice": -30})
        shard.commit("t1")
        shard.commit("t1")  # coordinator re-drive after a crash
        self.assertEqual(shard.data["alice"], 70)
        self.assertEqual(shard.locks, {})

    def test_reprepare_after_restart_keeps_single_vote(self):
        shard = Participant("A", {"alice": 100})
        shard.prepare("t1", {"alice": -30})
        shard.crash()
        shard.restart()
        self.assertTrue(shard.prepare("t1", {"alice": -30}))
        self.assertEqual(shard.outcome("t1"), "prepared")
        self.assertEqual(shard.locks, {"alice": "t1"})


class CoordinatorLogTests(unittest.TestCase):
    def _logs(self, coord):
        return [(r["t"], r.get("tx")) for r in coord.log.records()]

    def test_begin_record_is_durable_before_prepare_returns(self):
        shards = {"A": Participant("A", {"alice": 100}), "B": Participant("B", {"bob": 100})}
        coord = Coordinator(shards)
        coord.run("t1", {"A": {"alice": -30}, "B": {"bob": 30}})
        kinds = [r["t"] for r in coord.log.records()]
        self.assertEqual(kinds, ["begin", "decision"])

    def test_decision_record_precedes_participant_notification(self):
        shards = {"A": Participant("A", {"alice": 100}), "B": Participant("B", {"bob": 100})}
        coord = Coordinator(shards)
        try:
            coord.run("t1", {"A": {"alice": -30}, "B": {"bob": 30}}, crash="after_first_decision")
        except Exception:
            pass
        # Even though one shard already committed, the decision is on disk.
        self.assertEqual(coord.outcome("t1"), "commit")


if __name__ == "__main__":
    unittest.main()

