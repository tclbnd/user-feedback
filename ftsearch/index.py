"""Inverted index made of a write buffer plus frozen segments.

Deletes and updates are versioned: every write to a doc (add, update or
delete) bumps ``versions[doc]`` and each posting records the version it was
written with.  A posting is visible only while its version is still the
doc's latest one, so a delete or update takes effect immediately no matter
which segment holds the older copy.  Merges drop superseded and deleted
postings for good; tombstones are garbage-collected once no segment can
still reference them.
"""
from .segment import Segment
from .tokenizer import terms


class InvertedIndex:
    def __init__(self):
        self.segments = []
        self.merges = 0
        self.seq = 0
        self.versions = {}   # doc -> version of the latest write (bumped on delete too)
        self.deleted = set()  # docs whose latest write is a delete (tombstones)
        self.buffer = self._new_segment()

    def _new_segment(self):
        self.seq += 1
        return Segment(self.seq)

    def _bump(self, doc):
        self.versions[doc] = self.versions.get(doc, 0) + 1
        return self.versions[doc]

    # writes --------------------------------------------------------------
    def add(self, doc, text):
        self.update(doc, text)  # re-adding a doc replaces its old content

    def update(self, doc, text):
        version = self._bump(doc)
        self.deleted.discard(doc)
        self.buffer.remove(doc)
        self.buffer.add(doc, text, version)

    def delete(self, doc):
        self._bump(doc)  # tombstone: newer than any posting of this doc
        self.deleted.add(doc)
        self.buffer.remove(doc)

    def flush(self):
        if self.buffer.docs:
            self.segments.append(self.buffer.freeze())
            self.buffer = self._new_segment()

    # reads ---------------------------------------------------------------
    def _all(self):
        return self.segments + [self.buffer]

    def _live(self, doc, version):
        """A posting is visible only if it is the doc's latest version."""
        return self.versions.get(doc) == version

    def postings(self, term):
        best = {}  # doc -> (version, positions) of the newest posting
        for seg in self._all():
            for doc, positions in seg.postings.get(term, {}).items():
                ver = seg.versions[doc]
                if doc not in best or ver > best[doc][0]:
                    best[doc] = (ver, positions)
        return {doc: positions for doc, (ver, positions) in best.items()
                if self._live(doc, ver)}

    def search(self, query):
        words = terms(query)
        if not words:
            return []
        docs = set(self.postings(words[0]))
        for w in words[1:]:
            docs &= set(self.postings(w))
        return sorted(docs)

    def phrase(self, query):
        words = terms(query)
        if not words:
            return []
        plists = [self.postings(w) for w in words]
        docs = set(plists[0])
        for p in plists[1:]:
            docs &= set(p)
        hits = []
        for doc in docs:
            pos = [set(p[doc]) for p in plists]
            if any(all(start + k in pos[k] for k in range(1, len(words)))
                   for start in pos[0]):
                hits.append(doc)
        return sorted(hits)

    # maintenance -----------------------------------------------------------
    def _gc_tombstones(self):
        """Forget deletes no remaining segment can still reference."""
        for doc in list(self.deleted):
            if any(doc in seg.docs for seg in self.segments):
                continue
            self.deleted.discard(doc)
            self.versions.pop(doc, None)

    def stats(self):
        return {"segments": len(self.segments), "postings": sum(s.size() for s in self._all()),
                "merges": self.merges}
