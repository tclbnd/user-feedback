import unittest

from connpool import handlers
from connpool.pool import Pool, PoolExhausted
from connpool.server import Server


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


if __name__ == "__main__":
    unittest.main()
