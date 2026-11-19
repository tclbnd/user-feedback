"""Inverted index made of a write buffer plus frozen segments."""
from .segment import Segment
from .tokenizer import terms


class InvertedIndex:
    def __init__(self):
        self.segments = []
        self.merges = 0
        self.seq = 0
        self.buffer = self._new_segment()

    def _new_segment(self):
        self.seq += 1
        return Segment(self.seq)

    # writes --------------------------------------------------------------
    def add(self, doc, text):
        self.buffer.add(doc, text)

    def update(self, doc, text):
        self.buffer.remove(doc)
        self.buffer.add(doc, text)

    def delete(self, doc):
        self.buffer.remove(doc)

    def flush(self):
        if self.buffer.docs:
            self.segments.append(self.buffer.freeze())
            self.buffer = self._new_segment()

    # reads ---------------------------------------------------------------
    def _all(self):
        return self.segments + [self.buffer]

    def postings(self, term):
        out = {}
        for seg in self._all():
            for doc, positions in seg.postings.get(term, {}).items():
                out.setdefault(doc, []).extend(positions)
        return out

    def search(self, query):
        words = terms(query)
        if not words:
            return []
        docs = set(self.postings(words[0]))
        for w in words[1:]:
            docs &= set(self.postings(w))
        return sorted(docs)

    def phrase(self, query):
        return self.search(query)

    # maintenance -----------------------------------------------------------
    def stats(self):
        return {"segments": len(self.segments), "postings": sum(s.size() for s in self._all()),
                "merges": self.merges}
