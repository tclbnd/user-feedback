"""Chooses between a full scan and a partial index scan."""
from .implication import implies
from .predicates import truth


class PartialIndex:
    def __init__(self, name, column, predicate, rows):
        self.name = name
        self.column = column
        self.predicate = predicate
        self.rows = [r for r in rows if truth(predicate, r) is True]

    def size(self):
        return len(self.rows)


def choose(query, indexes):
    usable = [ix for ix in indexes if implies(query, ix.predicate)]
    return min(usable, key=lambda ix: ix.size()) if usable else None


def execute(query, rows, indexes):
    ix = choose(query, indexes)
    source = ix.rows if ix else rows
    out = sorted(r["id"] for r in source if truth(query, r) is True)
    return {"plan": ix.name if ix else "seq scan", "ids": out, "rows_read": len(source)}
