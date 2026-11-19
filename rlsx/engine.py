"""Statement execution with row-level security.

For a session (role, user, tenant) the engine decides which existing rows a
statement may see or touch (the policies' USING expressions) and which new
rows it may write (the WITH CHECK expressions).
"""
from .expr import evaluate


class PolicyViolation(Exception):
    pass


class Engine:
    def __init__(self, catalog):
        self.catalog = catalog

    def _applies(self, table, role):
        t = self.catalog.tables[table]
        return t.get("rls", False)

    def _using(self, table, command, row, session):
        if not self._applies(table, session["role"]):
            return True
        pols = self.catalog.policies_for(table, command, session["role"])
        if not pols:
            return True
        return all(evaluate(p["using"], row, session) for p in pols if p.get("using") is not None)

    def _check(self, table, command, row, session):
        if not self._applies(table, session["role"]):
            return True
        pols = self.catalog.policies_for(table, command, session["role"])
        return all(evaluate(p["check"], row, session) for p in pols if p.get("check") is not None)

    def select(self, table, session, where=None):
        rows = self.catalog.tables[table]["rows"]
        return [r for r in rows if self._using(table, "select", r, session)
                and (where is None or evaluate(where, r, session))]

    def insert(self, table, row, session):
        if not self._check(table, "insert", row, session):
            return 0
        self.catalog.tables[table]["rows"].append(dict(row))
        return 1

    def update(self, table, where, changes, session):
        n = 0
        for r in self.catalog.tables[table]["rows"]:
            if where is not None and not evaluate(where, r, session):
                continue
            if not self._using(table, "update", r, session):
                continue
            new = dict(r, **changes)
            if not self._check(table, "update", new, session):
                continue
            r.update(changes)
            n += 1
        return n

    def delete(self, table, where, session):
        rows = self.catalog.tables[table]["rows"]
        keep, n = [], 0
        for r in rows:
            if (where is None or evaluate(where, r, session)) and self._using(table, "delete", r, session):
                n += 1
            else:
                keep.append(r)
        self.catalog.tables[table]["rows"] = keep
        return n
