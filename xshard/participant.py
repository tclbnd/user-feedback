"""A shard taking part in distributed transactions.

A shard votes in ``prepare``: it locks the rows the transaction touches and
makes the pending writes durable before answering yes.  After that it must
keep the locks and wait for the coordinator's decision.

Durability invariants:

* A yes vote is only returned after the prepare record (writes plus the
  locked rows) is synced.  A crash at any later point therefore restarts
  with the transaction still prepared, its locks re-acquired, so nothing
  can touch those rows while the outcome is in doubt.
* ``commit`` / ``abort`` records are synced before the in-memory state is
  changed and the locks released, so a crash between applying the outcome
  and releasing the locks cannot undo it.  Both calls are idempotent:
  receiving the same decision twice (coordinator re-drive after a crash)
  never double-applies the writes.
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
        # Idempotent: a retransmitted prepare for the same transaction keeps
        # the vote it already gave instead of recording it a second time.
        if txid in self.prepared:
            return True
        for key in writes:
            holder = self.locks.get(key)
            if holder is not None and holder != txid:
                return False
        for key in writes:
            self.locks[key] = txid
        self.prepared[txid] = dict(writes)
        # Synced (Log.append defaults to sync=True): the locks and pending
        # writes survive a crash before the coordinator's decision arrives.
        self.log.append({"t": "prepare", "tx": txid, "writes": writes})
        return True

    def commit(self, txid):
        self._check()
        writes = self.prepared.pop(txid, None)
        if writes is None:
            # Already committed (or never prepared): nothing to re-apply.
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
        """Ask the coordinator about every transaction still in doubt.

        A ``None`` answer means the coordinator has no durable decision yet
        (it may still be recovering); the transaction stays prepared with
        its locks held and a later resolve pass will finish it.
        """
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
                # Re-acquire the row locks: a prepared transaction is still
                # in doubt, so local writes on those rows must keep being
                # refused until a commit/abort record resolves it.
                self.prepared[record["tx"]] = record["writes"]
                for key in record["writes"]:
                    self.locks[key] = record["tx"]
            elif kind == "commit":
                for key, delta in self.prepared.pop(record["tx"], {}).items():
                    self.data[key] = self.data.get(key, 0) + delta
                self._unlock(record["tx"])
            elif kind == "abort":
                self.prepared.pop(record["tx"], None)
                self._unlock(record["tx"])
