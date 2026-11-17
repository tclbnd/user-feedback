"""Statement execution: INSERT ... ON CONFLICT, UPDATE, DELETE, transactions."""
from constraints import Constraints, Violation


def evaluate(expr, row, excluded=None):
    if not isinstance(expr, dict):
        return expr
    if "col" in expr:
        return row.get(expr["col"])
    if "excluded" in expr:
        return excluded.get(expr["excluded"])
    a, b = (evaluate(x, row, excluded) for x in expr.get("add") or expr["sub"])
    if a is None or b is None:
        return None
    return a + b if "add" in expr else a - b


def matches(where, row):
    for col, cond in (where or {}).items():
        if isinstance(cond, dict) and row.get(col) not in cond["in"]:
            return False
        if not isinstance(cond, dict) and (row.get(col) is None or row.get(col) != cond):
            return False
    return True


class Session:
    def __init__(self, table, rows=()):
        self.columns = table["columns"]
        self.cons = Constraints(table.get("constraints", []))
        self.rows, self.journal, self.next_id, self.in_tx = {}, [], 1, False
        for row in rows:
            self._write(None, row)
        self.journal = []

    def _write(self, rid, row):
        if rid is None:
            rid, self.next_id = self.next_id, self.next_id + 1
        self.journal.append((rid, self.rows.get(rid)))
        self.rows[rid] = {c: row.get(c) for c in self.columns}
        return rid

    def _undo(self, mark):
        while len(self.journal) > mark:
            rid, old = self.journal.pop()
            if old is None:
                del self.rows[rid]
            else:
                self.rows[rid] = old

    def execute(self, stmt):
        op = stmt["op"]
        if op == "begin":
            self.in_tx = True
            return {"ok": True}
        if op in ("commit", "rollback"):
            return self._finish(op == "commit")
        autocommit, self.in_tx = not self.in_tx, True
        modes = dict(self.cons.mode)
        try:
            result = getattr(self, "_" + op)(stmt)
        except Violation as v:
            self.cons.mode = modes
            if autocommit:
                self._finish(False)
            return {"ok": False, "error": v.info}
        if autocommit:
            done = self._finish(True)
            if not done["ok"]:
                return done
        return {"ok": True, **result}

    def _finish(self, commit):
        self.in_tx = False
        try:
            if commit:
                self.cons.check(self.rows, self.cons.in_mode("deferred"), "commit")
        except Violation as v:
            self._undo(0)
            return {"ok": False, "error": v.info}
        finally:
            if not commit:
                self._undo(0)
            self.journal = []
            self.cons.reset()
        return {"ok": True}

    def _set_constraints(self, stmt):
        names = [c.name for c in self.cons.all if c.deferrable] if stmt["names"] == "all" else stmt["names"]
        for name in names:
            c = self.cons.get(name)
            if c is None or not c.deferrable:
                raise Violation("not_deferrable", name)
            self.cons.mode[name] = stmt["mode"]
        if stmt["mode"] == "immediate":
            self.cons.check(self.rows, [self.cons.get(n) for n in names], "statement")
        return {}

    def _insert(self, stmt):
        conflict = stmt.get("on_conflict") or {}
        target = conflict.get("target")
        if conflict.get("action") == "update" and (target is None or self.cons.mode.get(target) != "immediate"):
            raise Violation("invalid_target", target)
        counts = {"inserted": 0, "updated": 0, "skipped": 0}
        if not conflict:
            seen = {}
            for i, raw in enumerate(stmt["rows"]):
                for c in self.cons.all:
                    key = c.key(raw)
                    if key is not None and (c.name, key) in seen:
                        raise Violation("unique_violation", c.name, key, seen[c.name, key])
                    seen.setdefault((c.name, key), i)
        for i, raw in enumerate(stmt["rows"]):
            row = {c: raw.get(c) for c in self.columns}
            hit = self.cons.find_conflict(self.rows, row, target)
            if hit is None:
                self._write(None, row)
                counts["inserted"] += 1
                continue
            c, rid = hit
            if not conflict or (target is not None and target != c.name):
                raise Violation("unique_violation", c.name, c.key(row), i)
            if conflict["action"] == "nothing":
                counts["skipped"] += 1
                continue
            old = self.rows[rid]
            self._write(rid, {**old, **{k: evaluate(e, old, row) for k, e in conflict["set"].items()}})
            counts["updated"] += 1
        self.cons.check(self.rows, self.cons.in_mode("immediate"), "statement")
        return counts

    def _update(self, stmt):
        hits = [rid for rid, row in sorted(self.rows.items()) if matches(stmt.get("where"), row)]
        for rid in hits:
            old = self.rows[rid]
            self._write(rid, {**old, **{k: evaluate(e, old) for k, e in stmt["set"].items()}})
            self.cons.check(self.rows, self.cons.in_mode("immediate"), "statement")
        return {"updated": len(hits)}

    def _delete(self, stmt):
        hits = [rid for rid, row in sorted(self.rows.items()) if matches(stmt.get("where"), row)]
        for rid in hits:
            self.journal.append((rid, self.rows.pop(rid)))
        return {"deleted": len(hits)}

    def _select(self, stmt):
        return {"rows": [r for _, r in sorted(self.rows.items()) if matches(stmt.get("where"), r)]}
