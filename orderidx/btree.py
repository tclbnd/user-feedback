"""B-tree indexes with per-column ordering options.

CREATE INDEX ... (a [ASC|DESC] [NULLS FIRST|LAST], b ...).  The defaults
are ASC NULLS LAST, and DESC NULLS FIRST.  The index keeps its entries in
exactly that order; it can be read forwards or backwards.
"""
from .sortkeys import normalize, sort_rows


class BTree:
    def __init__(self, name, columns):
        self.name = name
        # Index columns default to ASC NULLS LAST / DESC NULLS FIRST, exactly
        # like CREATE INDEX in PostgreSQL; the entries are stored in that order.
        self.columns = [normalize(c) for c in columns]

    def build(self, rows):
        self.entries = sort_rows(rows, self.columns)
        return self

    def scan(self, backward=False, prefix=None):
        """Entries in index order (or reversed); ``prefix`` restricts the leading
        columns to given values (equality conditions)."""
        out = self.entries
        if prefix:
            out = [r for r in out if all(r.get(c) == v for c, v in prefix.items())]
        return list(reversed(out)) if backward else list(out)
