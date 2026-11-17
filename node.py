"""Database node: replicated log, term, lease-guarded writes and log reconciliation."""


class Node:
    def __init__(self, name, rate=1.0):
        self.name, self.rate = name, rate
        self.role, self.term, self.log = "replica", 1, []
        self.lease_start, self.alive, self.conflicts = None, True, []

    def local(self, t):
        return t * self.rate

    def last(self):
        return (self.log[-1]["term"], len(self.log)) if self.log else (0, 0)

    def lease_deadline(self, lease, drift):
        """Local-clock instant at which the lease granted by the controller ends."""
        if self.lease_start is None:
            return None
        # A slow local clock must not stretch the lease in real time: the
        # controller can promote at `granted + lease` on its own clock, so the
        # holder gives up `lease * drift` early.  With rate >= 1 - drift the
        # real-time deadline is at most `granted + lease`.
        return self.lease_start + lease * (1.0 - drift)

    def lease_valid(self, t, lease, drift):
        deadline = self.lease_deadline(lease, drift)
        if deadline is None:
            return False
        # The epsilon keeps a floored division from handing out one extra tick
        # on the boundary; it only ever shortens the lease, never extends it.
        return self.local(t) < deadline - 1e-9

    def writable(self, t, lease, drift):
        return self.alive and self.role == "primary" and self.lease_valid(t, lease, drift)

    def grant_lease(self, t):
        self.lease_start = self.local(t)

    def step_down(self, term):
        """Stop being primary; the term never moves backwards."""
        self.role, self.term = "replica", max(self.term, term)
        self.lease_start = None

    def resign(self):
        """Give up the primary role after the lease ran out, keeping the term."""
        self.role, self.lease_start = "replica", None

    def fork_point(self, other_log):
        """Longest prefix of both logs that carries identical entries.

        Index + term would be enough if a term had at most one primary, which
        the lease guarantees; comparing key and value as well keeps the fork
        computation honest even if a scenario manages to produce same-term
        entries that differ.
        """
        limit = min(len(self.log), len(other_log))
        index = 0
        while index < limit and self._same(self.log[index], other_log[index]):
            index += 1
        return index

    @staticmethod
    def _same(a, b):
        return (a["term"], a["key"], a["value"]) == (b["term"], b["key"], b["value"])

    def receive(self, leader):
        """Follow `leader`, rolling divergent local entries into `conflicts`.

        Returns the reconciliation record, or False when the push is fenced.
        The fence is the storage-layer rule: an entry or log tagged with a
        term older than the one this node already adopted is refused, and so
        is a second primary claiming the very same term.
        """
        if leader is self:
            return True
        if leader.term < self.term:
            return False
        if leader.term == self.term and self.role == "primary":
            # Two primaries in one term is a bug in the lease protocol, not a
            # normal state.  Never let one overwrite the other's log: refuse,
            # the sender steps down too, and the next election (higher term)
            # reconciles both logs into their conflict files.
            return False
        before = [dict(entry) for entry in self.log]
        fork = self.fork_point(leader.log)
        rolled = before[fork:]
        self.conflicts.extend(
            {"index": fork + offset + 1, "term": entry["term"],
             "key": entry["key"], "value": entry["value"]}
            for offset, entry in enumerate(rolled))
        self.log = before[:fork] + [dict(entry) for entry in leader.log[fork:]]
        self.role, self.term = "replica", leader.term
        self.lease_start = None
        return {"fork": fork,
                "rolled_back": [{"index": fork + offset + 1, "term": entry["term"],
                                 "key": entry["key"], "value": entry["value"]}
                                for offset, entry in enumerate(rolled)]}

    def kv(self):
        state = {}
        for entry in self.log:
            state[entry["key"]] = entry["value"]
        return state

    def snapshot(self):
        return {"role": self.role, "term": self.term, "alive": self.alive,
                "log": [[e["term"], e["key"], e["value"]] for e in self.log], "kv": self.kv()}
