"""Write-ahead log: one JSON record per line."""
import json
import os


class Log:
    def __init__(self, path):
        self.path = path
        if not os.path.exists(path):
            open(path, "wb").close()
        records = self.records()
        self.last = records[-1]["lsn"] if records else 0

    def records(self):
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def append(self, record):
        self.last += 1
        record = {"lsn": self.last, **record}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record
