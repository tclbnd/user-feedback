"""A shard taking part in distributed transactions.

A shard votes in ``prepare``: it locks the rows the transaction touches and
makes the pending writes durable before answering yes.  After that it must
keep the locks and wait for the coordinator's decision.
"""
from .disk import Disk
from .log import Log


class Down(Exception):
    pass


class LockConflict(Exception):
    pass


class Participant:
    def __init__(self, name, balances=None):
        self.name = name
        self.disk = Disk()
        self.log = Log(self.disk, "shard.log")
        self.up = True
        self.data = dict(balances or {})
        self.log.append({"t": "base", "data": self.data})
        self.locks = {}
        self.prepared = {}

    def _check(self):
        if not self.up:
            raise Down(self.name)

    # two-phase commit ---------------------------------------------------
    def prepare(self, txid, writes):
        self._check()
        for key in writes:
            holder = self.locks.get(key)
            if holder is not None and holder != txid:
                return False
        for key in writes:
            self.locks[key] = txid
        self.prepared[txid] = dict(writes)
        self.log.append({"t": "prepare", "tx": txid, "writes": writes})
        return True

    def commit(self, txid):
        self._check()
        writes = self.prepared.pop(txid, None)
        if writes is None:
            return
        for key, delta in writes.items():
            self.data[key] = self.data.get(key, 0) + delta
        self.log.append({"t": "commit", "tx": txid})
        self._unlock(txid)

    def abort(self, txid):
        self._check()
        if self.prepared.pop(txid, None) is not None:
            self.log.append({"t": "abort", "tx": txid})
        self._unlock(txid)

    def _unlock(self, txid):
        for key in [k for k, v in self.locks.items() if v == txid]:
            del self.locks[key]

    def resolve(self, coordinator):
        """Ask the coordinator about every transaction still in doubt."""
        self._check()
        for txid in sorted(self.prepared):
            decision = coordinator.outcome(txid)
            if decision == "commit":
                self.commit(txid)
            elif decision == "abort":
                self.abort(txid)

    def outcome(self, txid):
        if txid in self.prepared:
            return "prepared"
        for record in self.log.records():
            if record.get("tx") == txid and record["t"] in ("commit", "abort"):
                return {"commit": "committed", "abort": "aborted"}[record["t"]]
        return "unknown"

    # local writes -----------------------------------------------------------
    def write(self, key, delta):
        self._check()
        if key in self.locks:
            raise LockConflict(f"{self.name}:{key} locked by {self.locks[key]}")
        self.data[key] = self.data.get(key, 0) + delta
        self.log.append({"t": "local", "key": key, "delta": delta})

    # failures ---------------------------------------------------------------
    def crash(self):
        self.up = False
        self.disk.crash()

    def restart(self):
        self.up = True
        self.data, self.locks, self.prepared = {}, {}, {}
        for record in self.log.records():
            kind = record["t"]
            if kind == "base":
                self.data = dict(record["data"])
            elif kind == "local":
                self.data[record["key"]] = self.data.get(record["key"], 0) + record["delta"]
            elif kind == "prepare":
                self.prepared[record["tx"]] = record["writes"]
            elif kind == "commit":
                for key, delta in self.prepared.pop(record["tx"], {}).items():
                    self.data[key] = self.data.get(key, 0) + delta
            elif kind == "abort":
                self.prepared.pop(record["tx"], None)
