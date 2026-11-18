"""A tiny transactional store for the order service.

Tables are dicts keyed by primary key.  Every committed change is written to
the WAL and the WAL is synced at commit, so committed rows survive a crash.
"""
from .sequence import Sequence
from .wal import Wal


class UniqueViolation(Exception):
    pass


class Transaction:
    def __init__(self, db):
        self.db = db
        self.writes = []
        self.saved = {name: seq.snapshot() for name, seq in db.sequences.items()}
        self.done = False

    def nextval(self, name):
        return self.db.sequences[name].nextval()

    def insert(self, table, key, row):
        rows = self.db.tables.setdefault(table, {})
        if key in rows or any(t == table and k == key for t, k, _ in self.writes):
            raise UniqueViolation(f"{table}: duplicate key {key}")
        self.writes.append((table, key, row))

    def commit(self):
        for table, key, row in self.writes:
            self.db.wal.append({"op": "insert", "table": table, "key": key, "row": row})
        self.db.wal.flush()
        for table, key, row in self.writes:
            self.db.tables.setdefault(table, {})[key] = row
        self.done = True

    def rollback(self):
        for name, value in self.saved.items():
            self.db.sequences[name].restore(value)
        self.writes.clear()
        self.done = True


class Database:
    def __init__(self, disk, sequences=(("orders_id", 20),)):
        self.disk = disk
        self.wal = Wal(disk)
        self.tables = {}
        self.sequences = {name: Sequence(disk, name, cache) for name, cache in sequences}
        self._recover()

    def _recover(self):
        for record in self.wal.records():
            self.apply(record)

    def apply(self, record):
        if record["op"] == "insert":
            self.tables.setdefault(record["table"], {})[record["key"]] = record["row"]

    def begin(self):
        return Transaction(self)

    def shutdown(self):
        for seq in self.sequences.values():
            seq.checkpoint()
        self.wal.flush()
