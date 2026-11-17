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

    def lease_valid(self, t, lease, drift):
        if self.lease_start is None:
            return False
        return self.local(t) < self.lease_start + lease

    def writable(self, t, lease, drift):
        return self.alive and self.role == "primary" and self.lease_valid(t, lease, drift)

    def step_down(self, term):
        self.role, self.term = "replica", max(self.term, term)

    def receive(self, leader):
        """Follow `leader`: drop what it does not have, copy what we miss."""
        self.role, self.term = "replica", leader.term
        fork = min(len(self.log), len(leader.log))
        for index in range(fork, len(self.log)):
            self.conflicts.append({"index": index + 1, **self.log[index]})
        self.log = self.log[:fork] + [dict(entry) for entry in leader.log[fork:]]
        return True

    def kv(self):
        state = {}
        for entry in self.log:
            state[entry["key"]] = entry["value"]
        return state

    def snapshot(self):
        return {"role": self.role, "term": self.term, "alive": self.alive,
                "log": [[e["term"], e["key"], e["value"]] for e in self.log], "kv": self.kv()}
