"""Asynchronous log shipping from a primary to one standby."""
from .disk import Disk
from .engine import Database
from .wal import WAL


class Standby:
    def __init__(self):
        self.disk = Disk()
        self.applied = 0
        self.db = Database(self.disk)

    def receive(self, records):
        for record in records:
            if record["lsn"] <= self.applied:
                continue
            self.disk.append(WAL, _line(record))
            self.db.apply(record)
            self.applied = record["lsn"]
        self.disk.sync(WAL)

    def promote(self):
        """Turn the standby into a primary that accepts writes."""
        return Database(self.disk)


def ship(primary, standby):
    standby.receive(primary.wal.since(standby.applied))


def _line(record):
    import json
    return json.dumps(record, sort_keys=True) + "\n"
