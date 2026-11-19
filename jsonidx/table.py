"""Document table with an optional path index and a tiny planner."""
from .path import matches
from .pathindex import PathIndex


class DocTable:
    def __init__(self, indexed=True):
        self.docs = {}
        self.index = PathIndex() if indexed else None

    def insert(self, doc_id, doc):
        self.docs[doc_id] = doc
        if self.index is not None:
            self.index.add(doc_id, doc)

    def replace(self, doc_id, doc):
        """Store a new version of a document (the API's PUT)."""
        self.docs[doc_id] = doc
        if self.index is not None:
            self.index.add(doc_id, doc)

    def delete(self, doc_id):
        self.docs.pop(doc_id, None)

    def scan(self, pred):
        return sorted(i for i, d in self.docs.items() if matches(d, pred))

    def query(self, pred):
        """Use the index when it supports the predicate, else scan."""
        if self.index is not None and self.index.supports(pred):
            return {"plan": "index", "ids": sorted(self.index.lookup(pred))}
        return {"plan": "scan", "ids": self.scan(pred)}
