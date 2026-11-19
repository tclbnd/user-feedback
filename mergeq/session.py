"""Transactions over a set of tables."""
from .merge import MergeError
from .table import ConstraintError


class Session:
    def __init__(self, tables):
        self.tables = tables
        self._snap = None

    def begin(self):
        self._snap = {n: t.snapshot() for n, t in self.tables.items()}

    def commit(self):
        self._snap = None

    def rollback(self):
        if self._snap is not None:
            for n, snap in self._snap.items():
                self.tables[n].restore(snap)
        self._snap = None

    def run(self, fn, *args, **kwargs):
        """Run one statement inside the open transaction."""
        try:
            return {"ok": True, "result": fn(*args, **kwargs)}
        except (MergeError, ConstraintError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
