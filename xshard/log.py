"""Append-only JSON-lines log kept on a node's disk."""
import json


class Log:
    def __init__(self, disk, name):
        self.disk = disk
        self.name = name

    def append(self, record, sync=True):
        self.disk.append(self.name, json.dumps(record, sort_keys=True) + "\n")
        if sync:
            self.disk.sync(self.name)

    def records(self):
        return [json.loads(line) for line in self.disk.read(self.name).splitlines() if line.strip()]
