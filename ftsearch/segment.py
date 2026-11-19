"""Index segments.

The write buffer collects postings in memory; ``flush`` freezes it into an
immutable segment.  A posting list maps doc id -> sorted token positions.
"""
from collections import defaultdict

from .tokenizer import tokens


class Segment:
    def __init__(self, seq):
        self.seq = seq
        self.postings = defaultdict(dict)   # term -> {doc: [positions]}
        self.docs = set()
        self.frozen = False

    def add(self, doc, text):
        assert not self.frozen
        self.docs.add(doc)
        for pos, term in tokens(text):
            self.postings[term].setdefault(doc, []).append(pos)

    def remove(self, doc):
        assert not self.frozen
        self.docs.discard(doc)
        for term in list(self.postings):
            self.postings[term].pop(doc, None)
            if not self.postings[term]:
                del self.postings[term]

    def freeze(self):
        self.frozen = True
        return self

    def size(self):
        return sum(len(p) for p in self.postings.values())
