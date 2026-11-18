"""Transaction coordinator running two-phase commit across shards.

Durability invariants (the order on disk is the whole protocol):

1. A ``begin`` record is synced before any participant is asked to prepare,
   so after a restart the coordinator still knows every transaction that
   may have prepared shards waiting for an answer.
2. The ``decision`` record is synced *before* any participant is told the
   outcome.  A participant can therefore only observe a decision that the
   coordinator will remember after any number of crashes, which rules out
   split outcomes.
3. Recovery re-drives every known transaction to its single outcome and
   keeps re-sending it until every shard reaches a terminal state.  A
   transaction with a durable ``begin`` but no durable decision is aborted
   (presumed abort): no shard could have received a decision without the
   durable decision record, so abort is the only safe and convergent
   choice.  Every step is idempotent and survives repeated crashes.
"""
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
        # Invariant 1: the transaction must be known durably before shards
        # can lock rows and vote.  Without this a crash after prepare would
        # leave prepared shards nobody ever comes back for.
        self.log.append({"t": "begin", "tx": txid, "shards": parts})
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
        # Invariant 2: the decision is write-ahead.  It hits disk before any
        # participant is contacted, so a crash during the notification phase
        # can never erase the only copy of the outcome.
        self.log.append({"t": "decision", "tx": txid, "decision": decision})
        self._drive(txid, decision, parts, crash)
        self.active.pop(txid, None)
        return decision

    def _drive(self, txid, decision, parts=None, crash=None):
        """Tell every shard the outcome.  Safe to call over and over.

        Shards that are down, already terminal, or that missed an earlier
        attempt are simply retried; participant commit/abort are idempotent.
        """
        parts = parts or self._shard_list(txid)
        for i, name in enumerate(parts):
            if name not in self.shards:
                continue
            shard = self.shards[name]
            try:
                if shard.outcome(txid) == "prepared":
                    getattr(shard, decision)(txid)
            except Down:
                pass
            if crash == "after_first_decision" and i == 0:
                raise Crash(txid)

    def _shard_list(self, txid):
        for record in self.log.records():
            if record["t"] == "begin" and record["tx"] == txid:
                return list(record["shards"])
        return sorted(self.shards)

    def outcome(self, txid):
        for record in self.log.records():
            if record["t"] == "decision" and record["tx"] == txid:
                return record["decision"]
        return None

    def crash(self):
        self.disk.crash()
        self.active = {}

    def recover(self):
        """Converge every transaction the coordinator ever started.

        For each durable ``begin``: if no decision survived, write a durable
        abort (presumed abort), then re-send the outcome to any shard that
        is still in doubt.  Running this repeatedly, including after further
        crashes, produces the same outcome everywhere and releases the locks
        once the shards apply it.
        """
        known = []
        for record in self.log.records():
            if record["t"] == "begin":
                known.append((record["tx"], list(record["shards"])))
        for txid, parts in known:
            decision = self.outcome(txid)
            if decision is None:
                # No durable decision => no participant could have been told
                # one; abort is the only convergent resolution.
                decision = "abort"
                self.log.append({"t": "decision", "tx": txid, "decision": decision})
            self._drive(txid, decision, parts)
