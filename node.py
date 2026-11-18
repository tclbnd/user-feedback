"""Database node: replicated log, term, lease-guarded writes and log reconciliation."""

# At an exact boundary the lease counts as expired: the controller may promote
# a replacement at the same instant, and floating point error could otherwise
# make the comparison succeed by a rounding artifact.
_EPS = 1e-9


class Node:
    def __init__(self, name, rate=1.0):
        self.name, self.rate = name, float(rate)
        self.role, self.term, self.log = "replica", 1, []
        self.lease_start, self.alive, self.conflicts = None, True, []
        # Every entry this node ever appended, in order.  Rolled-back entries
        # must all show up in `conflicts`, which is what the auditor checks.
        self.history = []
        # Details of the most recent receive(): fork index and rolled-back
        # entries.  None until the first push is processed.
        self.last_push = None

    def local(self, t):
        return t * self.rate

    def last(self):
        return (self.log[-1]["term"], len(self.log)) if self.log else (0, 0)

    def lease_valid(self, t, lease, drift):
        """Is the lease valid on *this node's* clock?

        A clock may run as slow as ``1 - drift``, so the lease is only safe
        until its earliest possible expiry, ``lease_start + lease * (1 -
        drift)``.  By then the controller's own clock has reached the timeout
        and it is free to promote somebody else.  Omitting the ``(1 - drift)``
        factor lets a slow primary keep accepting writes after its
        replacement is elected -- the double-primary bug.
        """
        if self.lease_start is None:
            return False
        expiry = self.lease_start + lease * (1.0 - drift)
        return self.local(t) < expiry - _EPS

    def writable(self, t, lease, drift):
        return self.alive and self.role == "primary" and self.lease_valid(t, lease, drift)

    def store_write(self, term, key, value):
        """Storage-layer fence: append only if `term` is still the current term.

        The lease check happens above this layer; this is the last line of
        defence, so a request that was in flight when the node got fenced by a
        newer term can never reach the log.
        """
        if term < self.term:
            return False
        entry = {"term": term, "key": key, "value": value}
        self.log.append(entry)
        self.history.append(entry)
        return True

    def crash(self):
        """Stop serving, forget the lease, keep log and term (they are durable)."""
        if not self.alive:
            return
        self.alive, self.lease_start = False, None

    def restart(self):
        """Come back as a replica with the durable log and term intact.

        Restarting a node that is already up is a no-op; otherwise a stray
        restart event would silently demote a healthy primary.
        """
        if self.alive:
            return
        self.alive, self.role, self.lease_start = True, "replica", None

    def step_down(self, term):
        self.role, self.term = "replica", max(self.term, term)
        self.lease_start = None

    def fork_point(self, leader):
        """Length of the longest prefix whose entries carry the leader's terms.

        A term belongs to exactly one primary, so equal terms at equal indexes
        mean the same entry; the first mismatch is the start of the fork.
        """
        for index, (mine, theirs) in enumerate(zip(self.log, leader.log)):
            if mine["term"] != theirs["term"]:
                return index
        return min(len(self.log), len(leader.log))

    def receive(self, leader):
        """Follow `leader`; return False when fenced by a newer local term.

        Entries after the fork point are rolled back into ``conflicts`` (with
        their 1-based indexes) before the leader's suffix is copied.  A fenced
        push mutates nothing, so the sender can safely report it as `fenced`.

        Two nodes must never both consider themselves primary in the same
        term; if that state is ever reached (it is unreachable by
        construction), fencing on the equal term keeps the storage layer
        rather than letting the logs silently overwrite each other.
        """
        if leader.term < self.term:
            self.last_push = {"leader": leader.name, "accepted": False,
                              "fork": None, "rolled_back": [], "reason": "stale term"}
            return False
        if leader.term == self.term and self.role == "primary":
            self.last_push = {"leader": leader.name, "accepted": False,
                              "fork": None, "rolled_back": [], "reason": "concurrent primary"}
            return False
        fork = self.fork_point(leader)
        rolled = [{"index": index + 1, **self.log[index]} for index in range(fork, len(self.log))]
        self.conflicts.extend(rolled)
        adopted = [dict(entry) for entry in leader.log[fork:]]
        self.log = self.log[:fork] + adopted
        self.history.extend(adopted)
        self.role, self.term = "replica", max(self.term, leader.term)
        self.lease_start = None
        self.last_push = {"leader": leader.name, "accepted": True,
                          "fork": fork, "rolled_back": rolled, "reason": None}
        return True

    def kv(self):
        state = {}
        for entry in self.log:
            state[entry["key"]] = entry["value"]
        return state

    def snapshot(self):
        return {"role": self.role, "term": self.term, "alive": self.alive,
                "log": [[e["term"], e["key"], e["value"]] for e in self.log], "kv": self.kv()}
