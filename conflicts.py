"""Read registrations, rw-antidependency edges and dangerous structures."""


class ConflictTracker:
    def __init__(self):
        self.points, self.ranges, self.edges = {}, {}, []

    def register(self, tx, lo, hi=None):
        if hi is None:
            self.points.setdefault(tx.name, set()).add(lo)
        else:
            self.ranges.setdefault(tx.name, []).append((lo, hi))

    def forget(self, tx):
        self.points.pop(tx.name, None)
        self.ranges.pop(tx.name, None)

    def readers_of(self, key):
        names = {n for n, keys in self.points.items() if key in keys}
        names |= {n for n, rs in self.ranges.items() if any(lo <= key <= hi for lo, hi in rs)}
        return names

    def add_edge(self, reader, writer):
        pair = (reader.name, writer.name)
        if reader is writer or pair in self.edges or "aborted" in (reader.status, writer.status):
            return False
        self.edges.append(pair)
        return True

    def is_pivot(self, tx, txns):
        """A transaction with both an incoming and an outgoing edge closes a cycle."""
        live = [(a, b) for a, b in self.edges if "aborted" not in (txns[a].status, txns[b].status)]
        return any(b == tx.name for a, b in live) and any(a == tx.name for a, b in live)
