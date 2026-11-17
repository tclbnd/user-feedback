"""Executes DELETE and UPDATE statements with referential actions."""


class FkError(Exception):
    def __init__(self, fk, child, parent, phase):
        super().__init__(fk.name)
        self.info = {"code": "foreign_key_violation", "constraint": fk.name,
                     "row": [fk.table, child], "at": list(parent) if parent else None, "phase": phase}


class Statement:
    def __init__(self, db):
        self.db = db
        self.deleted, self.updated, self.pending = set(), set(), []
        self.view = db.snapshot()

    def delete_row(self, table, pk):
        row = self.db.rows[table][pk]
        self._propagate(table, pk, row, row, "on_delete")
        del self.db.rows[table][pk]
        self.deleted.add((table, pk))

    def update_row(self, table, pk, changes):
        old = self.db.rows[table].pop(pk)
        new = {**old, **changes}
        self.db.rows[table][new[self.db.pk(table)]] = new
        self.updated.add((table, new[self.db.pk(table)]))
        self._propagate(table, pk, old, new, "on_update")

    def _propagate(self, table, pk, old, new, event):
        for fk in self.db.referencing(table):
            value = old.get(fk.ref_column)
            if value is None:
                continue
            action = getattr(fk, event)
            for child in self.db.children(fk, value, self.view):
                if action in ("restrict", "no_action"):
                    raise FkError(fk, child, (table, pk), "immediate")
                elif action == "cascade" and event == "on_delete":
                    self.delete_row(fk.table, child)
                elif action == "cascade":
                    self.update_row(fk.table, child, {fk.column: new[fk.ref_column]})
                elif action == "set_null":
                    self.update_row(fk.table, child, {fk.column: None})
                elif action == "set_default":
                    self.update_row(fk.table, child, {fk.column: self.db.default(fk.table, fk.column)})
                    self.pending.append((fk, child, (table, pk)))

    def finish(self):
        for fk, child, parent in self.pending:
            row = self.db.rows[fk.table].get(child)
            if row is not None and row.get(fk.column) is not None and not self.db.parent_exists(fk, row[fk.column]):
                raise FkError(fk, child, parent, "statement_end")


def execute(db, stmt):
    before = db.snapshot()
    st = Statement(db)
    table = stmt["table"]
    targets = [pk for pk, row in sorted(db.rows[table].items())
               if all(row.get(c) == v for c, v in stmt.get("where", {}).items())]
    try:
        for pk in targets:
            if stmt["op"] == "delete":
                st.delete_row(table, pk)
            elif pk in db.rows[table]:
                st.update_row(table, pk, stmt["set"])
        st.finish()
    except FkError as exc:
        db.rows = before
        return {"ok": False, "error": exc.info}
    count = lambda keys: {t: sum(1 for k in keys if k[0] == t) for t in sorted({k[0] for k in keys})}
    return {"ok": True, "deleted": count(st.deleted), "updated": count(st.updated - st.deleted)}
