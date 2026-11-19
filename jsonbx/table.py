"""A table with a jsonb column, optional GIN indexes and a small planner."""
from .gin import OPCLASSES
from .ops import OPERATORS
from .value import parse


class DocTable:
    def __init__(self, opclasses=("jsonb_ops",)):
        self.rows = {}
        self.indexes = {name: OPCLASSES[name]() for name in opclasses}

    def insert(self, rid, text):
        doc = parse(text) if isinstance(text, str) else text
        if rid in self.rows:
            self.delete(rid)
        self.rows[rid] = doc
        for ix in self.indexes.values():
            ix.insert(rid, doc)

    def delete(self, rid):
        doc = self.rows.pop(rid)
        for ix in self.indexes.values():
            ix.delete(rid, doc)

    def scan(self, op, arg):
        fn = OPERATORS[op]
        return sorted(r for r, d in self.rows.items() if fn(d, arg))

    def query(self, op, arg, opclass=None):
        """Use the named index (or the first that supports op), recheck, else scan."""
        names = [opclass] if opclass else list(self.indexes)
        for name in names:
            ix = self.indexes.get(name)
            if ix is not None and op in ix.operators:
                fn = OPERATORS[op]
                ids = sorted(r for r in ix.search(op, arg) if r in self.rows and fn(self.rows[r], arg))
                return {"plan": name, "ids": ids}
        return {"plan": "seq scan", "ids": self.scan(op, arg)}
