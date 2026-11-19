"""Segment merging.

Small segments accumulate as the buffer is flushed.  Merging combines
segments into one so queries touch fewer posting lists.

``merge_segments`` merges everything at once (the nightly optimize).  The
merger that runs during the day is size-tiered: it picks the few smallest
segments, which are not necessarily adjacent in flush order, and runs in
the background: ``BackgroundMerge.begin`` takes the chosen segments as they
are at that moment and builds the merged segment from them while writes
continue, and ``commit`` swaps the merged segment in for the ones it was
built from.  Segments flushed in between are left as they are.
"""
from .segment import Segment

TIER_SIZE = 3


def _build(segments):
    merged = Segment(max(seg.seq for seg in segments))
    for seg in segments:
        merged.docs |= seg.docs
        for term, plist in seg.postings.items():
            for doc, positions in plist.items():
                merged.postings[term].setdefault(doc, []).extend(positions)
    return merged.freeze()


def pick_tier(index, size=TIER_SIZE):
    """The ``size`` smallest segments (by posting count, then flush order)."""
    if len(index.segments) < 2:
        return []
    ranked = sorted(index.segments, key=lambda s: (s.size(), s.seq))
    return sorted(ranked[:size], key=lambda s: s.seq)


class BackgroundMerge:
    def __init__(self, index, segments):
        self.index = index
        self.sources = list(segments)
        self.merged = None

    def begin(self):
        if len(self.sources) >= 2:
            self.merged = _build(self.sources)
        return self

    def commit(self):
        if self.merged is None:
            return False
        ids = {id(s) for s in self.sources}
        kept = [s for s in self.index.segments if id(s) not in ids]
        self.index.segments = sorted(kept + [self.merged], key=lambda s: s.seq)
        self.index.merges += 1
        return True


def merge_segments(index, max_segments=1):
    if len(index.segments) <= max_segments:
        return False
    return BackgroundMerge(index, index.segments).begin().commit()


def merge_tier(index):
    return BackgroundMerge(index, pick_tier(index)).begin().commit()
