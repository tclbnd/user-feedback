"""Read routing with session consistency."""
from cluster import Unavailable


class Router:
    def __init__(self, cluster, policy):
        self.cluster = cluster
        self.on_stale = policy.get("on_stale", "primary")
        self.max_wait = policy.get("max_wait", 0)
        self.probe_interval = policy.get("probe_interval", 100)
        self.cursor = 0

    def candidates(self, t, skip=()):
        return [(i, r) for i, r in enumerate(self.cluster.replicas) if r.name not in skip]

    def pick(self, t, required, skip=()):
        eligible = {i for i, r in self.candidates(t, skip) if r.applied(t) >= required}
        n = len(self.cluster.replicas)
        for step in range(n):
            index = (self.cursor + step) % n
            if index in eligible:
                self.cursor = (index + 1) % n
                return self.cluster.replicas[index]
        return None

    def read(self, key, t, required):
        deadline = t + self.max_wait if self.on_stale == "wait" else t
        failed = []
        while True:
            replica = self.pick(t, required, failed)
            if replica is None:
                waits = [r.eligible_at(required) for _, r in self.candidates(t, failed)
                         if r.eligible_at(required) <= deadline]
                if self.on_stale == "wait" and waits:
                    t = max(t, min(waits))
                    continue
                t = deadline
                return "primary", t, self.cluster.read("primary", key, t), failed
            try:
                return replica.name, t, self.cluster.read(replica.name, key, t), failed
            except Unavailable:
                failed.append(replica.name)
