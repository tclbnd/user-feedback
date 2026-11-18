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
