"""Cancellation, statement_timeout and disconnect must unwind cleanly.

Reproductions of the two production incidents plus the explicit-transaction
and savepoint state rules that PostgreSQL guarantees.
"""
import unittest

from cancel.scheduler import run


def upd(key, value, work=1, table="t"):
    return {"cmd": "update", "table": table, "key": key, "value": value, "work": work}


def outcome(results, cmd):
    return [r["outcome"] for r in results if r["cmd"] == cmd]


class CancelAutocommitTests(unittest.TestCase):
    def test_cancel_autocommit_update_releases_locks_and_unblocks_writer(self):
        # Incident 1: DBA cancels a long autocommit UPDATE; everything it
        # touched must be unlocked so later writes do not queue forever.
        spec = {
            "data": {"t": {"a": 0}},
            "sessions": {
                "batch": {"script": [upd("a", 1, work=20)]},
                "app": {"script": [upd("a", 2)]},
            },
            "cancel": [{"tick": 4, "session": "batch"}],
        }
        out = run(spec)
        self.assertEqual(out["results"]["batch"][0]["outcome"], "canceled")
        self.assertEqual(out["data"]["t"]["a"], 2)       # half-written value discarded
        self.assertEqual(out["locks"], {})
        self.assertEqual(out["waiting"], {})
        self.assertEqual(out["stuck"], [])
        self.assertEqual(out["states"]["batch"], "idle")

    def test_cancel_while_waiting_for_lock_withdraws_queue_request(self):
        # Cancelling while blocked must remove the queued request; the lock
        # must never be granted to a session whose statement already ended.
        spec = {
            "data": {"t": {"a": 0}},
            "sessions": {
                "holder": {"script": [{"cmd": "begin"}, upd("a", 1, work=10), {"cmd": "commit"}]},
                "victim": {"script": [upd("a", 2, work=0)]},
                "next": {"script": [upd("a", 3, work=0)]},
            },
            "cancel": [{"tick": 3, "session": "victim"}],
        }
        out = run(spec)
        self.assertEqual(out["results"]["victim"][0]["outcome"], "canceled")
        self.assertEqual(out["data"]["t"]["a"], 3)
        self.assertEqual(out["locks"], {})
        self.assertEqual(out["stuck"], [])


class TimeoutTests(unittest.TestCase):
    def test_timeout_waiting_on_lock_does_not_leak_grant(self):
        # Incident 2: report query waits past statement_timeout.  Its queued
        # request is withdrawn, so when the holder commits nothing is granted
        # to the finished report session and following writers proceed.
        spec = {
            "data": {"accounts": {"1": 100}},
            "sessions": {
                "holder": {"script": [{"cmd": "begin"},
                                      upd("1", 80, work=8, table="accounts"),
                                      {"cmd": "commit"}]},
                "report": {"statement_timeout": 3, "script": [
                    {"cmd": "lock_table", "table": "accounts", "mode": "S", "work": 1}]},
                "app": {"script": [{"cmd": "begin"},
                                   upd("1", 70, work=0, table="accounts"),
                                   {"cmd": "commit"}]},
            },
        }
        out = run(spec)
        self.assertEqual(out["results"]["report"][0]["outcome"], "timeout")
        self.assertEqual(out["states"]["report"], "idle")
        self.assertEqual(out["data"]["accounts"]["1"], 70)
        self.assertEqual(out["locks"], {})
        self.assertEqual(out["waiting"], {})
        self.assertEqual(out["stuck"], [])

    def test_timeout_during_work_in_autocommit_releases_locks(self):
        spec = {
            "data": {"t": {"a": 0}},
            "sessions": {"s1": {"statement_timeout": 3, "script": [upd("a", 1, work=10)]},
                         "s2": {"script": [upd("a", 2)]}},
        }
        out = run(spec)
        self.assertEqual(out["results"]["s1"][0]["outcome"], "timeout")
        self.assertEqual(out["data"]["t"]["a"], 2)
        self.assertEqual(out["locks"], {})
        self.assertEqual(out["stuck"], [])


class ExplicitTransactionTests(unittest.TestCase):
    def test_cancel_in_explicit_txn_marks_it_failed_and_commit_rolls_back(self):
        # Incident 3: a cancelled statement must abort the transaction; the
        # later COMMIT becomes a rollback and cannot publish partial writes.
        spec = {
            "data": {"t": {"a": 0, "b": 0}},
            "sessions": {"s1": {"script": [
                {"cmd": "begin"},
                upd("a", 1, work=0),
                upd("b", 2, work=10),
                {"cmd": "commit"},
            ]}},
            "cancel": [{"tick": 4, "session": "s1"}],
        }
        out = run(spec)
        self.assertEqual(out["states"]["s1"], "idle")
        self.assertEqual(outcome(out["results"]["s1"], "commit"), ["rolled back"])
        self.assertEqual(out["data"]["t"], {"a": 0, "b": 0})
        self.assertEqual(out["locks"], {})

    def test_statements_after_failure_are_rejected_until_rollback(self):
        spec = {
            "data": {"t": {"a": 0}},
            "sessions": {"s1": {"script": [
                {"cmd": "begin"}, upd("a", 1, work=10),
                upd("a", 2, work=0),
                {"cmd": "rollback"},
            ]}},
            "cancel": [{"tick": 3, "session": "s1"}],
        }
        out = run(spec)
        self.assertEqual(out["results"]["s1"][2]["outcome"],
                         "error: current transaction is aborted")
        self.assertEqual(out["data"]["t"]["a"], 0)
        self.assertEqual(out["locks"], {})

    def test_explicit_txn_keeps_granted_locks_until_rollback(self):
        # A statement already holding locks when cancelled keeps them for the
        # failed transaction; the waiter only proceeds once it is rolled back.
        spec = {
            "data": {"t": {"a": 0}},
            "sessions": {
                "s1": {"script": [{"cmd": "begin"}, upd("a", 1, work=10), {"cmd": "rollback"}]},
                "s2": {"script": [upd("a", 2, work=0)]},
            },
            "cancel": [{"tick": 4, "session": "s1"}],
        }
        out = run(spec)
        self.assertEqual(out["results"]["s1"][0]["outcome"], "canceled")
        self.assertEqual(out["data"]["t"]["a"], 2)
        self.assertEqual(out["stuck"], [])


class DisconnectTests(unittest.TestCase):
    def test_disconnect_releases_row_and_table_locks_and_unblocks_writer(self):
        # The holder disconnects mid-transaction: row locks must be released
        # too (the old code only dropped table locks), so the waiter proceeds.
        spec = {
            "data": {"t": {"a": 0}},
            "sessions": {
                "holder": {"script": [{"cmd": "begin"}, upd("a", 1, work=5),
                                      {"cmd": "disconnect"}]},
                "waiter": {"script": [upd("a", 2, work=0)]},
            },
        }
        out = run(spec)
        self.assertEqual(out["states"]["holder"], "closed")
        self.assertEqual(out["data"]["t"]["a"], 2)
        self.assertEqual(out["locks"], {})
        self.assertEqual(out["waiting"], {})
        self.assertEqual(out["stuck"], [])

    def test_disconnect_while_waiting_does_not_block_later_grants(self):
        spec = {
            "data": {"t": {"a": 0}},
            "sessions": {
                "holder": {"script": [{"cmd": "begin"}, upd("a", 1, work=8), {"cmd": "commit"}]},
                "gone": {"script": [{"cmd": "disconnect"}, upd("a", 9, work=0)]},
                "next": {"script": [upd("a", 3, work=0)]},
            },
        }
        out = run(spec)
        self.assertEqual(out["states"]["gone"], "closed")
        self.assertEqual(out["data"]["t"]["a"], 3)
        self.assertEqual(out["locks"], {})
        self.assertEqual(out["stuck"], [])


class SavepointTests(unittest.TestCase):
    def test_rollback_to_savepoint_releases_locks_taken_after_it(self):
        # The settlement batch pattern: a record's statement is cancelled
        # while holding its row lock.  ROLLBACK TO SAVEPOINT must drop that
        # lock so the blocked competing writer and the next records proceed.
        script = [
            {"cmd": "begin"},
            {"cmd": "savepoint", "name": "rec0"},
            upd("a", 1, work=10),
            {"cmd": "rollback_to", "name": "rec0", "on_error": True},
            {"cmd": "release", "name": "rec0"},
            upd("b", 2, work=0),
            {"cmd": "commit"},
        ]
        spec = {
            "data": {"t": {"a": 0, "b": 0}},
            "sessions": {
                "batch": {"script": script},
                "app": {"script": [upd("a", 9, work=0)]},
            },
            "cancel": [{"tick": 4, "session": "batch"}],
        }
        out = run(spec)
        self.assertEqual(out["results"]["batch"][1]["outcome"], "canceled")
        self.assertEqual(out["results"]["batch"][2]["outcome"], "ok")
        self.assertEqual(out["data"]["t"], {"a": 9, "b": 2})
        self.assertEqual(out["locks"], {})
        self.assertEqual(out["stuck"], [])

    def test_rollback_to_savepoint_keeps_earlier_locks_and_ends_failed_state(self):
        script = [
            {"cmd": "begin"},
            upd("a", 1, work=0),
            {"cmd": "savepoint", "name": "p"},
            upd("b", 2, work=10),
            {"cmd": "rollback_to", "name": "p"},
            upd("b", 3, work=0),
            {"cmd": "commit"},
        ]
        spec = {
            "data": {"t": {"a": 0, "b": 0}},
            "sessions": {"batch": {"script": script}},
            "cancel": [{"tick": 5, "session": "batch"}],
        }
        out = run(spec)
        self.assertEqual(out["data"]["t"], {"a": 1, "b": 3})
        self.assertEqual(out["states"]["batch"], "idle")
        self.assertEqual(out["locks"], {})


if __name__ == "__main__":
    unittest.main()
