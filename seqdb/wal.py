"""Write-ahead log: one JSON record per line in the file ``wal``."""
import json

WAL = "wal"


class Wal:
    def __init__(self, disk):
        self.disk = disk
        self.lsn = len(self.records())

    def records(self):
        out = []
        for line in self.disk.read(WAL).splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def append(self, record):
        self.lsn += 1
        record = dict(record, lsn=self.lsn)
        self.disk.append(WAL, json.dumps(record, sort_keys=True) + "\n")
        return record

    def flush(self):
        self.disk.sync(WAL)

    def since(self, lsn):
        return [r for r in self.records() if r["lsn"] > lsn]
