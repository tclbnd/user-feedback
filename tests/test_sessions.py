import unittest

from cancel.scheduler import run


def upd(key, value, work=1):
    return {"cmd": "update", "table": "t", "key": key, "value": value, "work": work}


class SessionTests(unittest.TestCase):
    def test_autocommit_updates_serialize(self):
        out = run({"data": {"t": {"a": 0}}, "sessions": {"s1": {"script": [upd("a", 1, 3)]}, "s2": {"script": [upd("a", 2)]}}})
        self.assertEqual(out["data"]["t"]["a"], 2)
        self.assertEqual(out["locks"], {})

    def test_explicit_rollback_discards(self):
        out = run({"data": {"t": {"a": 0}}, "sessions": {"s1": {"script": [{"cmd": "begin"}, upd("a", 5), {"cmd": "rollback"}]}}})
        self.assertEqual(out["data"]["t"]["a"], 0)
        self.assertEqual(out["locks"], {})

    def test_waiter_gets_lock_after_commit(self):
        out = run({"data": {"t": {"a": 0}}, "sessions": {
            "s1": {"script": [{"cmd": "begin"}, upd("a", 1, 4), {"cmd": "commit"}]},
            "s2": {"script": [upd("a", 2)]}}})
        self.assertEqual(out["data"]["t"]["a"], 2)
        self.assertEqual(out["stuck"], [])


if __name__ == "__main__":
    unittest.main()
