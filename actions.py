"""Executes DELETE and UPDATE statements with referential actions.

The cascade tree is expanded depth-first: the foreign keys that reference a
row's table are applied in definition order, and for each of them the
referencing rows in primary key order.  A row is removed before its own
referencing rows are visited; a row that is already gone is skipped, so
diamonds and cycles are each handled once.  An update only propagates through
a foreign key when the referenced column really changed.

``restrict`` fails as soon as the first referencing row is found.  ``no_action``
and ``set_default`` are checked when the whole statement has finished.
"""

IMMEDIATE = "immediate"
STATEMENT_END = "statement_end"


class FkError(Exception):
    def __init__(self, fk, child, parent, phase):
        super().__init__(fk.name)
        self.info = {"code": "foreign_key_violation", "constraint": fk.name,
                     "row": [fk.table, child], "at": list(parent) if parent else None,
                     "phase": phase}


class Statement:
    def __init__(self, db):
        self.db = db
        # Counts follow the logical row: a token is attached to a row when it
        # is first touched and moves with it if its primary key changes, so a
        # row handled through many paths is counted once.
        self._tokens = {}               # (table, pk) -> token
        self._position = {}             # token -> (table, pk)
        self._token_table = {}          # token -> table
        self._next_token = 0
        self.deleted, self.updated = set(), set()
        # Checks that can only be decided once the whole statement has run:
        # ("no_action", fk, removed value, parent row) and
        # ("set_default", fk, child token, parent row).
        self.checks = []

    # -- row identity ------------------------------------------------------
    def _token(self, table, pk):
        token = self._tokens.get((table, pk))
        if token is None:
            token = self._next_token
            self._next_token += 1
            self._tokens[(table, pk)] = token
            self._token_table[token] = table
        self._position[token] = (table, pk)
        return token

    def _move_token(self, table, old_pk, new_pk):
        token = self._tokens.pop((table, old_pk), None)
        if token is not None:
            self._tokens[(table, new_pk)] = token
            self._position[token] = (table, new_pk)

    # -- removing a row ----------------------------------------------------
    def delete_row(self, table, pk):
        frame = self._delete(table, pk)
        if frame is not None:
            self._drain(frame)

    def _delete(self, table, pk):
        """Remove one row, returning the frame that expands its children."""
        rows = self.db.rows[table]
        if pk not in rows:
            return None                 # already removed along another path
        row = rows.pop(pk)              # gone before its referencing rows are visited
        self.deleted.add(self._token(table, pk))
        return self._propagate(table, pk, row, None, "on_delete")

    # -- changing a row ----------------------------------------------------
    def update_row(self, table, pk, changes):
        frame = self._update(table, pk, changes)
        if frame is not None:
            self._drain(frame)

    def _update(self, table, pk, changes):
        """Change one row, returning the frame that expands its children."""
        rows = self.db.rows[table]
        if pk not in rows:
            return None                 # already removed along another path
        old = rows[pk]
        new = {**old, **changes}
        new_pk = new[self.db.pk(table)]
        self.updated.add(self._token(table, pk))
        if new_pk != pk:
            del rows[pk]
            self._move_token(table, pk, new_pk)
        rows[new_pk] = new
        return self._propagate(table, pk, old, new, "on_update")

    # -- depth-first expansion ---------------------------------------------
    def _drain(self, frame):
        """Walk the cascade tree depth-first using an explicit stack.

        Each frame lazily yields the action for one referencing row, so a
        child frame is fully expanded before the next sibling is produced.
        """
        stack = [frame]
        while stack:
            try:
                action = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            child = self._apply(action)
            if child is not None:
                stack.append(child)

    def _apply(self, action):
        kind, fk, row, replacement, parent = action
        if kind == "cascade_delete":
            return self._delete(fk.table, row)
        if kind == "cascade_update":
            return self._update(fk.table, row, {fk.column: replacement})
        if kind == "set_null":
            return self._update(fk.table, row, {fk.column: None})
        # set_default: the row may end up dangling, checked at statement end.
        default = self.db.default(fk.table, fk.column)
        token = self._token(fk.table, row)
        frame = self._update(fk.table, row, {fk.column: default})
        self.checks.append(("set_default", fk, token, parent))
        return frame

    def _propagate(self, table, pk, old, new, event):
        """Yield the action to take on each referencing row, in order."""
        for fk in self.db.referencing(table):
            value = old.get(fk.ref_column)
            if value is None:
                continue                # nothing references this value
            if event == "on_update":
                replacement = new.get(fk.ref_column)
                if replacement == value:
                    continue            # referenced column did not really change
            else:
                replacement = None
            action = getattr(fk, event)

            if action == "no_action":
                # Whether this is a violation depends on the final state.
                self.checks.append(("no_action", fk, value, (table, pk)))
                continue

            children = self.db.children(fk, value)
            if action == "restrict":
                if children:            # immediately, on the first referencing row
                    raise FkError(fk, children[0], (table, pk), IMMEDIATE)
                continue

            for child in children:
                kind = action
                if action == "cascade":
                    kind = "cascade_delete" if event == "on_delete" else "cascade_update"
                yield (kind, fk, child, replacement, (table, pk))

    # -- end of statement --------------------------------------------------
    def finish(self):
        for kind, fk, payload, parent in self.checks:
            if kind == "no_action":
                value = payload
                if self.db.parent_exists(fk, value):
                    continue
                children = self.db.children(fk, value)
                if children:
                    raise FkError(fk, children[0], parent, STATEMENT_END)
                continue
            # set_default: the child must still exist and point at a parent.
            child = self._position.get(payload)
            row = None if child is None else self.db.rows[fk.table].get(child[1])
            if row is None:
                continue                # that row itself was removed later on
            value = row.get(fk.column)
            if value is None or self.db.parent_exists(fk, value):
                continue
            raise FkError(fk, child[1], parent, STATEMENT_END)

    # -- result ------------------------------------------------------------
    def counts(self, tokens):
        tables = {self._token_table[token] for token in tokens}
        return {table: sum(1 for token in tokens if self._token_table[token] == table)
                for table in sorted(tables)}


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
    return {"ok": True, "deleted": st.counts(st.deleted),
            "updated": st.counts(st.updated - st.deleted)}
