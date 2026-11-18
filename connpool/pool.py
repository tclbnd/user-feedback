"""Client-side connection pool.

Connections are opened lazily up to ``size`` and reused afterwards.  The
statement cache on each pooled connection lets handlers prepare a statement
once per backend and execute it on every later request.
"""
from collections import deque

from .server import ServerError


class PoolExhausted(Exception):
    pass


class PooledConnection:
    def __init__(self, session):
        self.session = session
        self.statements = set()
        self.uses = 0

    def run_prepared(self, name, key):
        if name not in self.statements:
            self.session.prepare(name, key)
            self.statements.add(name)
        return self.session.execute(name)

    def __getattr__(self, item):
        return getattr(self.session, item)


class Pool:
    def __init__(self, server, size=4):
        self.server = server
        self.size = size
        self.idle = deque()
        self.busy = set()
        self.opened = 0

    def acquire(self):
        if self.idle:
            conn = self.idle.popleft()
        elif self.opened < self.size:
            conn = PooledConnection(self.server.connect())
            self.opened += 1
        else:
            raise PoolExhausted("no idle connection")
        conn.uses += 1
        self.busy.add(id(conn))
        return conn

    def release(self, conn):
        self.busy.discard(id(conn))
        self.idle.append(conn)

    def close(self):
        while self.idle:
            self.idle.popleft().session.close()
        self.opened = 0

    def stats(self):
        return {"opened": self.opened, "idle": len(self.idle), "busy": len(self.busy)}


class Borrowed:
    """``with Borrowed(pool) as conn:`` - releases the connection on exit."""

    def __init__(self, pool):
        self.pool = pool
        self.conn = None

    def __enter__(self):
        self.conn = self.pool.acquire()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.pool.release(self.conn)
        return False


__all__ = ["Pool", "Borrowed", "PoolExhausted", "ServerError"]
