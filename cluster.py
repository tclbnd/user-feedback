"""Simulated primary with replicas that replay commits after a lag."""


class Unavailable(Exception):
    pass


class Replica:
    def __init__(self, name, lag):
        self.name, self.lag, self.up = name, lag, True
        self.visible = []

    def replay(self, commit_time):
        previous = self.visible[-1] if self.visible else 0
        self.visible.append(max(previous, commit_time + self.lag))

    def applied(self, t):
        return sum(1 for at in self.visible if at <= t)

    def eligible_at(self, lsn):
        """Earliest time at which this replica has applied `lsn`."""
        return 0 if lsn == 0 else self.visible[lsn - 1]


class Cluster:
    def __init__(self, replicas):
        self.replicas = [Replica(r["name"], r["lag"]) for r in replicas]
        self.by_name = {r.name: r for r in self.replicas}
        self.lsn = 0
        self.versions = {}

    def commit(self, t, writes):
        self.lsn += 1
        for key, value in writes:
            self.versions.setdefault(key, []).append((self.lsn, value))
        for r in self.replicas:
            r.replay(t)
        return self.lsn

    def read(self, node, key, t):
        if node == "primary":
            upto = self.lsn
        else:
            replica = self.by_name[node]
            if not replica.up:
                raise Unavailable(node)
            upto = replica.applied(t)
        version, value = 0, None
        for lsn, v in self.versions.get(key, []):
            if lsn <= upto:
                version, value = lsn, v
        return {"value": value, "version": version, "applied": upto}
