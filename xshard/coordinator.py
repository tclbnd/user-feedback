"""Transaction coordinator running two-phase commit across shards."""
from .disk import Disk
from .log import Log
from .participant import Down


class Crash(Exception):
    """Raised by fault injection to stop the coordinator at a given point."""


class Coordinator:
    def __init__(self, shards):
        self.shards = shards
        self.disk = Disk()
        self.log = Log(self.disk, "coord.log")
        self.active = {}

    def run(self, txid, writes, crash=None):
        """``writes`` maps shard name -> {key: delta}.  Returns the decision."""
        parts = sorted(writes)
        self.active[txid] = parts
        self.log.append({"t": "begin", "tx": txid, "shards": parts}, sync=False)
        votes = []
        for name in parts:
            try:
                votes.append(self.shards[name].prepare(txid, writes[name]))
            except Down:
                votes.append(False)
            if crash == f"participant_after_prepare:{name}":
                self.shards[name].crash()
        if crash == "after_prepare":
            raise Crash(txid)
        decision = "commit" if all(votes) else "abort"
        for i, name in enumerate(parts):
            try:
                getattr(self.shards[name], decision)(txid)
            except Down:
                pass
            if crash == "after_first_decision" and i == 0:
                raise Crash(txid)
        self.log.append({"t": "decision", "tx": txid, "decision": decision})
        self.active.pop(txid, None)
        return decision

    def outcome(self, txid):
        for record in self.log.records():
            if record["t"] == "decision" and record["tx"] == txid:
                return record["decision"]
        return None

    def crash(self):
        self.disk.crash()
        self.active = {}

    def recover(self):
        """Re-send logged decisions to shards that may have missed them."""
        for record in self.log.records():
            if record["t"] != "decision":
                continue
            for shard in self.shards.values():
                try:
                    if shard.outcome(record["tx"]) == "prepared":
                        getattr(shard, record["decision"])(record["tx"])
                except Down:
                    pass
