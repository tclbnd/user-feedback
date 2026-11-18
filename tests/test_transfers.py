import unittest

from cluster import run

BASE = {"shards": {"A": {"alice": 100}, "B": {"bob": 100}}, "placement": {"alice": "A", "bob": "B"}}


class TransferTests(unittest.TestCase):
    def test_cross_shard_transfer_commits(self):
        out = run(dict(BASE, steps=[{"op": "transfer", "tx": "t1", "from": "alice", "to": "bob", "amount": 30}]))
        self.assertEqual(out["outcomes"]["t1"], {"A": "committed", "B": "committed"})
        self.assertEqual(out["report"]["total"], 200)
        self.assertEqual(out["locks"], {"A": {}, "B": {}})

    def test_local_write_blocked_by_lock(self):
        from xshard.participant import LockConflict, Participant
        shard = Participant("A", {"alice": 10})
        self.assertTrue(shard.prepare("t1", {"alice": -5}))
        with self.assertRaises(LockConflict):
            shard.write("alice", 1)
        shard.commit("t1")
        shard.write("alice", 1)
        self.assertEqual(shard.data["alice"], 6)


if __name__ == "__main__":
    unittest.main()
