"""Secondary index on a heap column.

``state`` follows the catalog: ``building`` (not yet maintained by writers,
not usable by queries), ``ready`` (writers maintain it, queries do not use
it yet) and ``valid`` (fully built, used by queries).
"""
from collections import defaultdict


class Index:
    def __init__(self, heap, column="key"):
        self.heap = heap
        self.column = column
        self.entries = defaultdict(set)
        self.state = "building"

    def add(self, key, tid):
        self.entries[key].add(tid)

    def remove(self, key, tid):
        """Drop one index entry (the tuple is dead or changed its key)."""
        tids = self.entries.get(key)
        if tids is None:
            return
        tids.discard(tid)
        if not tids:
            del self.entries[key]

    def contains(self, key, tid):
        return tid in self.entries.get(key, ())

    def compact(self):
        """Remove entries whose tuple is gone, dead or no longer has that key."""
        for key in list(self.entries):
            for tid in list(self.entries[key]):
                t = self.heap.tuples.get(tid)
                if not t or not t["live"] or t["key"] != key:
                    self.remove(key, tid)

    def lookup(self, key):
        out = []
        for tid in sorted(self.entries.get(key, ())):
            t = self.heap.tuples.get(tid)
            if t and t["live"] and t["key"] == key:
                out.append(t["val"])
        return sorted(out)

    def maintained(self):
        return self.state in ("ready", "valid")

    def usable(self):
        return self.state == "valid"
