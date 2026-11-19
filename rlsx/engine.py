"""Statement execution with row-level security.

For a session (role, user, tenant) the engine decides which existing rows a
statement may see or touch (the policies' USING expressions) and which new
rows it may write (the WITH CHECK expressions).  The rules follow
PostgreSQL:

* RLS applies only when the table has it enabled and the current role is
  neither a superuser, nor a BYPASSRLS role, nor the table owner (unless
  FORCE ROW LEVEL SECURITY is set; FORCE does not affect superusers or
  BYPASSRLS roles).
* PERMISSIVE policies are combined with OR, RESTRICTIVE policies with AND:
  a row must pass at least one permissive policy and every restrictive
  policy.  If no policy applies, the default is deny.
* SELECT and DELETE filter existing rows with USING; INSERT validates new
  rows with WITH CHECK; UPDATE filters the rows it may touch with USING and
  validates the new row contents with WITH CHECK.  A policy without WITH
  CHECK falls back to its USING expression for the check.
* UPDATE/DELETE statements with a WHERE clause read the table, so the USING
  expressions of SELECT policies apply to those rows as well.
* A row rejected by WITH CHECK (INSERT, or the new row of an UPDATE) raises
  PolicyViolation instead of being silently dropped.
"""
from .expr import evaluate


class PolicyViolation(Exception):
    pass


class Engine:
    def __init__(self, catalog):
        self.catalog = catalog

    def _applies(self, table, role):
        t = self.catalog.tables[table]
        if not t.get("rls", False):
            return False
        info = self.catalog.role_info(role)
        if info.get("superuser") or info.get("bypassrls"):
            return False
        if not t.get("force", False) and t.get("owner") in self.catalog.memberships(role):
            return False
        return True

    @staticmethod
    def _expr(pol, key):
        e = pol.get(key)
        if e is None and key == "check":
            # WITH CHECK omitted: the USING expression is used instead.
            e = pol.get("using")
        return e

    def _combine(self, pols, key, row, session):
        def passes(pol):
            e = self._expr(pol, key)
            return e is None or evaluate(e, row, session)

        permissive = [p for p in pols if p.get("kind", "permissive") == "permissive"]
        restrictive = [p for p in pols if p.get("kind", "permissive") != "permissive"]
        # No permissive policy (including no policies at all) means deny.
        return any(passes(p) for p in permissive) and all(passes(p) for p in restrictive)

    def _using(self, table, command, row, session):
        if not self._applies(table, session["role"]):
            return True
        pols = self.catalog.policies_for(table, command, session["role"])
        return self._combine(pols, "using", row, session)

    def _check(self, table, command, row, session):
        if not self._applies(table, session["role"]):
            return True
        pols = self.catalog.policies_for(table, command, session["role"])
        return self._combine(pols, "check", row, session)

    def select(self, table, session, where=None):
        rows = self.catalog.tables[table]["rows"]
        return [r for r in rows if self._using(table, "select", r, session)
                and (where is None or evaluate(where, r, session))]

    def insert(self, table, row, session):
        if not self._check(table, "insert", row, session):
            raise PolicyViolation(
                'new row violates row-level security policy for table "%s"' % table)
        self.catalog.tables[table]["rows"].append(dict(row))
        return 1

    def update(self, table, where, changes, session):
        n = 0
        for r in self.catalog.tables[table]["rows"]:
            if where is not None and not evaluate(where, r, session):
                continue
            if not self._using(table, "update", r, session):
                continue
            if where is not None and not self._using(table, "select", r, session):
                continue
            new = dict(r, **changes)
            if not self._check(table, "update", new, session):
                raise PolicyViolation(
                    'new row violates row-level security policy for table "%s"' % table)
            r.update(changes)
            n += 1
        return n

    def delete(self, table, where, session):
        rows = self.catalog.tables[table]["rows"]
        keep, n = [], 0
        for r in rows:
            if where is not None and not evaluate(where, r, session):
                keep.append(r)
                continue
            if not self._using(table, "delete", r, session):
                keep.append(r)
                continue
            if where is not None and not self._using(table, "select", r, session):
                keep.append(r)
                continue
            n += 1
        self.catalog.tables[table]["rows"] = keep
        return n
