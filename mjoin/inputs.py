"""Executor inputs with fetch accounting.

``SortedInput`` yields rows already ordered by the join key with NULLs last
(per column for composite keys), the way the sort node and ordered index
scans deliver them.  It supports ``mark``/``restore`` so a join can re-read
a run of equal keys.
"""
from .keys import key_of, sort_key


class SortedInput:
    def __init__(self, rows, key):
        self.rows = sorted(rows, key=lambda r: sort_key(key_of(r, key)))
        self.key = key
        self.pos = 0
        self.fetches = 0
        self._mark = None

    def peek(self):
        return self.rows[self.pos] if self.pos < len(self.rows) else None

    def next(self):
        row = self.peek()
        if row is not None:
            self.pos += 1
            self.fetches += 1
        return row

    def mark(self):
        self._mark = self.pos

    def restore(self):
        self.pos = self._mark

    def rewind(self):
        self.pos = 0
