"""TID bitmaps.

A bitmap maps page -> set of slots (exact) or the marker LOSSY (the whole
page may match).  When a bitmap holds more than ``work_mem`` exact TIDs,
the pages with most TIDs are turned lossy to save memory.  Rows found on a
lossy page, or on a page flagged ``recheck``, must have the original
condition evaluated again.
"""
LOSSY = "lossy"


class TidBitmap:
    def __init__(self, work_mem=64):
        self.pages = {}
        self.recheck = set()
        self.work_mem = work_mem

    @classmethod
    def from_tids(cls, tids, work_mem=64):
        bm = cls(work_mem)
        for page, slot in tids:
            bm.pages.setdefault(page, set()).add(slot)
        bm._shrink()
        return bm

    def _shrink(self):
        exact = sum(len(v) for v in self.pages.values() if v != LOSSY)
        for page in sorted(self.pages, key=lambda p: -len(self.pages[p]) if self.pages[p] != LOSSY else 0):
            if exact <= self.work_mem:
                break
            if self.pages[page] != LOSSY:
                exact -= len(self.pages[page])
                self.pages[page] = LOSSY

    def intersect(self, other):
        out = TidBitmap(self.work_mem)
        for page in sorted(set(self.pages) & set(other.pages)):
            a, b = self.pages[page], other.pages[page]
            if a == LOSSY and b == LOSSY:
                out.pages[page] = LOSSY
            elif a == LOSSY:
                out.pages[page] = set(b)
            elif b == LOSSY:
                out.pages[page] = set(a)
            else:
                common = a & b
                if common:
                    out.pages[page] = common
        out.recheck = (self.recheck | other.recheck) & set(out.pages)
        return out

    def union(self, other):
        out = TidBitmap(self.work_mem)
        for page in sorted(set(self.pages) | set(other.pages)):
            a, b = self.pages.get(page, set()), other.pages.get(page, set())
            if a == LOSSY:
                out.pages[page] = set(b) if b != LOSSY else LOSSY
            elif b == LOSSY:
                out.pages[page] = set(a)
            else:
                out.pages[page] = a | b
        out.recheck = (self.recheck | other.recheck) & set(out.pages)
        out._shrink()
        return out

    def lossy_pages(self):
        return sorted(p for p, v in self.pages.items() if v == LOSSY)
