"""A tiny transactional store for the order service.

Tables are dicts keyed by primary key.  Every committed change is written to
the WAL and the WAL is synced at commit, so committed rows survive a crash.

Sequence block reservations are also WAL records (``seqreserve``), synced
before the first value of a block is issued and shipped to the standby with
the same log stream.  Recovery rebuilds each sequence high-water mark from
those records, so a restarted primary or a promoted standby never reissues a
number.
"""
from .sequence import Sequence
from .wal import Wal


class UniqueViolation(Exception):
    pass


class Transaction:
    def __init__(self, db):
        self.db = db
        self.writes = []
        self.done = False

    def nextval(self, name):
        # Sequence state is not transactional: rollback must not reclaim
        # numbers that have already left the database.
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
        self.writes.clear()
        self.done = True


class Database:
    def __init__(self, disk, sequences=(("orders_id", 20),)):
        self.disk = disk
        self.wal = Wal(disk)
        self.tables = {}
        self.sequences = {
            name: Sequence(name, cache, persist=self._persist_reservation)
            for name, cache in sequences
        }
        self._recover()

    def _persist_reservation(self, name, reserved):
        """Durably record a sequence reservation.

        Appended and fsynced before any number in the new block is handed
        out, so the reservation survives a crash and is available to log
        shipping.  One sync per ``cache`` numbers, not one per number.
        """
        self.wal.append({"op": "seqreserve", "name": name, "reserved": reserved})
        self.wal.flush()

    def _recover(self):
        for record in self.wal.records():
            self.apply(record)

    def apply(self, record):
        if record["op"] == "insert":
            self.tables.setdefault(record["table"], {})[record["key"]] = record["row"]
        elif record["op"] == "seqreserve":
            seq = self.sequences.get(record["name"])
            if seq is not None:
                seq.replay_reserve(record["reserved"])

    def begin(self):
        return Transaction(self)

    def shutdown(self):
        # Nothing beyond the WAL needs syncing: every sequence reservation
        # was already fsynced when its block was allocated.
        self.wal.flush()
