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

A merge never resurrects old content: the merged segment keeps only the
newest version of each doc found in the sources, and ``commit`` re-checks
every surviving posting against the index's current versions, so docs
deleted or updated while the merge was running are dropped too.  After the
swap, tombstones that no segment can still reference are collected.
"""
from .segment import Segment

TIER_SIZE = 3


def _build(segments):
    """Merge sources, keeping only the newest version of each doc."""
    merged = Segment(max(seg.seq for seg in segments))
    latest = {}
    for seg in segments:
        for doc, ver in seg.versions.items():
            if ver > latest.get(doc, -1):
                latest[doc] = ver
    for seg in segments:
        for term, plist in seg.postings.items():
            for doc, positions in plist.items():
                if seg.versions[doc] != latest[doc]:
                    continue  # superseded by a newer version in another source
                merged.docs.add(doc)
                merged.versions[doc] = seg.versions[doc]
                merged.postings[term].setdefault(doc, []).extend(positions)
    return merged


def _drop_stale(segment, index):
    """Drop postings deleted or updated since ``segment`` was built."""
    stale = {doc for doc in segment.docs
             if not index._live(doc, segment.versions[doc])}
    if not stale:
        return
    segment.docs -= stale
    for doc in stale:
        segment.versions.pop(doc, None)
    for term in list(segment.postings):
        plist = segment.postings[term]
        for doc in stale:
            plist.pop(doc, None)
        if not plist:
            del segment.postings[term]


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
        # Writes went on while the merge was built: re-validate every
        # posting against the current versions before installing it.
        _drop_stale(self.merged, self.index)
        self.merged.freeze()
        ids = {id(s) for s in self.sources}
        kept = [s for s in self.index.segments if id(s) not in ids]
        self.index.segments = sorted(kept + [self.merged], key=lambda s: s.seq)
        self.index.merges += 1
        self.index._gc_tombstones()
        return True


def merge_segments(index, max_segments=1):
    if len(index.segments) <= max_segments:
        return False
    return BackgroundMerge(index, index.segments).begin().commit()


def merge_tier(index):
    return BackgroundMerge(index, pick_tier(index)).begin().commit()
