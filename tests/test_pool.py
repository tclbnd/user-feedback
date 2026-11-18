import unittest

from connpool import handlers
from connpool.pool import Borrowed, Pool, PoolExhausted
from connpool.server import DEFAULTS, Server


class PoolTests(unittest.TestCase):
    def test_connections_are_reused(self):
        server = Server()
        pool = Pool(server, size=2)
        for _ in range(10):
            handlers.plain_read(pool, "x")
        self.assertEqual(server.sessions_opened, 1)

    def test_pool_limit(self):
        pool = Pool(Server(), size=1)
        pool.acquire()
        with self.assertRaises(PoolExhausted):
            pool.acquire()

    def test_profile_roundtrip(self):
        server = Server()
        pool = Pool(server, size=1)
        handlers.update_profile(pool, "alice", "hello")
        self.assertEqual(handlers.get_profile(pool, "alice"), "hello")
        self.assertEqual(handlers.get_profile(pool, "alice"), "hello")

    # --- clean-state guarantees ------------------------------------------------
    def test_failed_transaction_is_rolled_back_before_reuse(self):
        server = Server()
        server.data[("public", "profile:alice")] = "v1"
        pool = Pool(server, size=1)

        with self.assertRaises(Exception):
            handlers.update_profile(pool, "alice", "half-written", fail=True)

        # The next request must not see the uncommitted write.
        self.assertEqual(handlers.plain_read(pool, "profile:alice"), "v1")
        self.assertEqual(
            sorted(server.data.items()),
            [(("public", "profile:alice"), "v1")],
        )
        with Borrowed(pool) as conn:
            self.assertFalse(conn.session.in_transaction())

    def test_timezone_does_not_leak_between_requests(self):
        pool = Pool(Server(), size=1)
        handlers.report_time(pool, "Asia/Shanghai")
        value = handlers.report_time(pool)
        self.assertTrue(value.endswith(" UTC"))
        with Borrowed(pool) as conn:
            self.assertEqual(conn.session.params, dict(DEFAULTS))

    def test_search_path_does_not_leak_between_requests(self):
        server = Server()
        server.data[("public", "profile:alice")] = "public-value"
        server.data[("tenant_b", "profile:alice")] = "tenant-value"
        pool = Pool(server, size=1)

        self.assertEqual(
            handlers.tenant_read(pool, "tenant_b", "profile:alice"),
            "tenant-value",
        )
        # A later request runs on the default search path again.
        self.assertEqual(
            handlers.plain_read(pool, "profile:alice"),
            "public-value",
        )
        with Borrowed(pool) as conn:
            self.assertEqual(conn.session.params, dict(DEFAULTS))

    def test_broken_idle_backend_is_replaced_not_reused(self):
        server = Server()
        pool = Pool(server, size=1)
        handlers.plain_read(pool, "k")
        self.assertEqual(server.sessions_opened, 1)

        # Proxy / failover drops the idle backend before the next request.
        pool.idle[0].session.broken = True

        handlers.plain_read(pool, "k")
        self.assertEqual(server.sessions_opened, 2)
        self.assertEqual(pool.stats()["opened"], 1)

    # --- throughput ------------------------------------------------------------
    def test_clean_requests_pay_no_extra_round_trips(self):
        server = Server()
        pool = Pool(server, size=1)
        start = server.roundtrips
        for _ in range(10):
            handlers.plain_read(pool, "x")
        # One connect + one read per request; no cleaning round trips.
        self.assertEqual(server.roundtrips - start, 11)

    def test_prepared_statement_cache_survives_parameter_reset(self):
        server = Server()
        pool = Pool(server, size=1)
        handlers.get_profile(pool, "alice")  # connect + prepare + execute
        handlers.tenant_read(pool, "tenant_b", "k")  # dirties search_path

        before = server.roundtrips
        handlers.get_profile(pool, "alice")
        # RESET ALL kept the prepared statement: one execute, no re-prepare.
        self.assertEqual(server.roundtrips - before, 1)


if __name__ == "__main__":
    unittest.main()
