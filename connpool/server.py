"""Simulated database server.

Each ``Session`` is one backend connection.  Session state (open transaction,
parameters set with SET, prepared statements) lives on the server side and
stays until the client changes it or the connection is closed.  Every call a
client makes is one network round trip.
"""
from datetime import datetime, timedelta

DEFAULTS = {"timezone": "UTC", "search_path": "public", "statement_timeout": 0}
OFFSETS = {"UTC": 0, "Asia/Shanghai": 8, "America/New_York": -5}


class ServerError(Exception):
    pass


class Server:
    def __init__(self):
        self.data = {}            # (schema, key) -> value, committed
        self.roundtrips = 0
        self.sessions_opened = 0
        self.clock = datetime(2026, 11, 18, 12, 0, 0)

    def connect(self):
        self.sessions_opened += 1
        self.roundtrips += 1
        return Session(self, self.sessions_opened)


class Session:
    def __init__(self, server, pid):
        self.server = server
        self.pid = pid
        self.params = dict(DEFAULTS)
        self.local_params = {}
        self.txn = None
        self.prepared = {}
        self.closed = False
        self.broken = False

    def _call(self):
        if self.closed:
            raise ServerError("connection closed")
        if self.broken:
            raise ServerError("server closed the connection unexpectedly")
        self.server.roundtrips += 1

    # parameters -------------------------------------------------------
    def set(self, name, value, local=False):
        self._call()
        if local:
            if self.txn is None:
                raise ServerError("SET LOCAL outside transaction")
            self.local_params[name] = value
        else:
            self.params[name] = value

    def show(self, name):
        self._call()
        return self.local_params.get(name, self.params[name])

    def reset_all(self):
        self._call()
        self.params = dict(DEFAULTS)

    def discard_all(self):
        self._call()
        if self.txn is not None:
            raise ServerError("DISCARD ALL cannot run inside a transaction block")
        self.params = dict(DEFAULTS)
        self.prepared.clear()

    # transactions -----------------------------------------------------
    def begin(self):
        self._call()
        if self.txn is None:
            self.txn = {}

    def commit(self):
        self._call()
        if self.txn is not None:
            self.server.data.update(self.txn)
        self.txn = None
        self.local_params.clear()

    def rollback(self):
        self._call()
        self.txn = None
        self.local_params.clear()

    def in_transaction(self):
        return self.txn is not None

    # data -------------------------------------------------------------
    def _key(self, key):
        return (self.show_path(), key)

    def show_path(self):
        return self.local_params.get("search_path", self.params["search_path"])

    def write(self, key, value):
        self._call()
        if self.txn is None:
            self.server.data[self._key(key)] = value
        else:
            self.txn[self._key(key)] = value

    def read(self, key):
        self._call()
        k = self._key(key)
        if self.txn is not None and k in self.txn:
            return self.txn[k]
        return self.server.data.get(k)

    def now(self):
        self._call()
        tz = self.local_params.get("timezone", self.params["timezone"])
        return (self.server.clock + timedelta(hours=OFFSETS[tz])).strftime("%Y-%m-%d %H:%M ") + tz

    # prepared statements ------------------------------------------------
    def prepare(self, name, key):
        self._call()
        self.prepared[name] = key

    def execute(self, name):
        self._call()
        if name not in self.prepared:
            raise ServerError(f'prepared statement "{name}" does not exist')
        k = (self.show_path(), self.prepared[name])
        if self.txn is not None and k in self.txn:
            return self.txn[k]
        return self.server.data.get(k)

    def close(self):
        self.closed = True
