"""Multi-version store.

Each committed write creates a version stamped with its transaction's
commit timestamp.  A read at snapshot ``ts`` sees, for every key, the
newest version committed at or before ``ts`` (a deletion is a version with
value None).
"""


class Store:
    def __init__(self):
        self.versions = {}     # (table, key) -> [(commit_ts, value)]
        self.ts = 0
        self.locked = False    # exclusive lock held by an offline dump
        self.log = []
        self.active_snapshots = {}   # holder -> ts; vacuum keeps what they can see

    def register_snapshot(self, holder, ts):
        self.active_snapshots[holder] = ts

    def release_snapshot(self, holder):
        self.active_snapshots.pop(holder, None)

    def now(self):
        return self.ts

    def commit(self, writes):
        """writes: list of (table, key, value-or-None).  Returns commit ts."""
        self.ts += 1
        for table, key, value in writes:
            self.versions.setdefault((table, key), []).append((self.ts, value))
        self.log.append({"ts": self.ts, "writes": [list(w) for w in writes]})
        return self.ts

    def read(self, table, key, ts):
        val = None
        for cts, v in self.versions.get((table, key), []):
            if cts <= ts:
                val = v
        return val

    def vacuum(self):
        """Drop versions no snapshot can see any more.  Returns versions removed."""
        horizon = min(self.active_snapshots.values(), default=self.ts)
        removed = 0
        for key in list(self.versions):
            vs = self.versions[key]
            visible = [i for i, (cts, _) in enumerate(vs) if cts <= horizon]
            keep_from = visible[-1] if visible else 0
            kept = vs[keep_from:]
            if kept and kept[0][0] <= horizon and kept[0][1] is None:
                kept = kept[1:]
            removed += len(vs) - len(kept)
            if kept:
                self.versions[key] = kept
            else:
                del self.versions[key]
        return removed

    def version_count(self):
        return sum(len(v) for v in self.versions.values())

    def keys(self, table, ts):
        return sorted(k for (t, k) in self.versions if t == table and self.read(t, k, ts) is not None)
