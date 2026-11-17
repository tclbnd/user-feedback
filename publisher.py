"""Publisher side of the replication protocol: change log, slot and stream."""
import json
import os


class ConnectionLost(Exception):
    pass


class Publisher:
    def __init__(self, directory):
        self.directory = directory
        self.slot_path = os.path.join(directory, "slot.json")
        with open(os.path.join(directory, "changes.jsonl"), encoding="utf-8") as fh:
            self.log = [json.loads(line) for line in fh if line.strip()]

    @property
    def confirmed(self):
        if not os.path.exists(self.slot_path):
            return 0
        with open(self.slot_path, encoding="utf-8") as fh:
            return json.load(fh)["confirmed_lsn"]

    def stream(self, start_lsn, drop_after=None):
        begin = max(start_lsn, self.confirmed)
        sent = 0
        for message in self.log:
            if message["lsn"] <= begin:
                continue
            if drop_after is not None and sent == drop_after:
                raise ConnectionLost(f"connection dropped after {sent} messages")
            sent += 1
            yield dict(message)

    def feedback(self, lsn):
        if lsn > self.confirmed:
            with open(self.slot_path, "w", encoding="utf-8") as fh:
                json.dump({"confirmed_lsn": lsn}, fh)
