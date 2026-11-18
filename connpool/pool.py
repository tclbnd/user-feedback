"""Client-side connection pool.

Connections are opened lazily up to ``size`` and reused afterwards.  The
statement cache on each pooled connection lets handlers prepare a statement
once per backend and execute it on every later request.

A connection is only reusable while it is *clean*: no open transaction and no
session parameters left over from the previous request.  Cleaning happens on
release, and only when something actually needs cleaning, so a request that
commits its transaction and never calls SET pays no extra round trips.  The
prepared-statement cache is kept across requests; broken backends are
discarded instead of being handed out again.
"""
from collections import deque

from .server import DEFAULTS, ServerError


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

    def _connect(self):
        conn = PooledConnection(self.server.connect())
        self.opened += 1
        return conn

    def _discard(self, conn):
        """Drop a connection that cannot be safely reused."""
        self.busy.discard(id(conn))
        try:
            conn.session.close()
        except ServerError:
            pass
        self.opened -= 1

    def _cleanup(self, conn):
        """Return a live connection to a clean state before it is reused.

        Only issues round trips for state that is actually dirty, and keeps
        the prepared-statement cache intact (RESET ALL does not touch it).
        """
        session = conn.session
        if session.in_transaction():
            session.rollback()
        if session.params != DEFAULTS:
            session.reset_all()

    def acquire(self):
        if self.idle:
            conn = self.idle.popleft()
            if conn.session.closed or conn.session.broken:
                # Backend died while idle (failover, proxy idle timeout).
                self._discard(conn)
                conn = self._connect()
        elif self.opened < self.size:
            conn = self._connect()
        else:
            raise PoolExhausted("no idle connection")
        # Defensive: release() is the normal cleaning point, but a connection
        # handed back out must never carry another request's state.
        self._cleanup(conn)
        conn.uses += 1
        self.busy.add(id(conn))
        return conn

    def release(self, conn):
        session = conn.session
        if session.closed or session.broken:
            self._discard(conn)
            return
        self._cleanup(conn)
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
        if self.conn is not None:
            self.pool.release(self.conn)
        return False


__all__ = ["Pool", "Borrowed", "PoolExhausted", "ServerError"]
