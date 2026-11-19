"""Heap pages.  A TID is (page, slot).

Deleted rows stay on the page (marked dead) until vacuum; indexes still
point at them.

An UPDATE writes a new version of the row (usually on the last page) and
marks the old version dead with ``next`` pointing at the new TID.  To keep
index maintenance cheap, only the indexes on columns the UPDATE changed get
an entry for the new TID; the other indexes keep pointing at the old
version, and readers reach the current version through the ``next`` chain
(``fetch_live``).  Vacuum, which rewrites those pointers, is not part of
this module.
"""

PAGE_SIZE = 8


class Heap:
    def __init__(self):
        self.pages = []

    def insert(self, row):
        if not self.pages or len(self.pages[-1]) >= PAGE_SIZE:
            self.pages.append([])
        self.pages[-1].append({"row": dict(row), "dead": False, "next": None})
        return (len(self.pages) - 1, len(self.pages[-1]) - 1)

    def delete(self, tid):
        self.pages[tid[0]][tid[1]]["dead"] = True

    def update(self, tid, changes):
        """Write a new version; returns (new tid, changed columns)."""
        old = self.fetch(tid)
        changed = sorted(c for c, v in changes.items() if old["row"].get(c) != v)
        new_tid = self.insert(dict(old["row"], **changes))
        old["dead"], old["next"] = True, new_tid
        return new_tid, changed

    def fetch(self, tid):
        return self.pages[tid[0]][tid[1]]

    def fetch_live(self, tid):
        """Follow the update chain from ``tid``; returns (tid, item) of the newest version."""
        item = self.fetch(tid)
        while item["next"] is not None:
            tid = item["next"]
            item = self.fetch(tid)
        return tid, item

    def page(self, n):
        return self.pages[n]

    def all_tids(self):
        for p, page in enumerate(self.pages):
            for s in range(len(page)):
                yield (p, s)
