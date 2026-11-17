"""Tables, rows and foreign key definitions."""
import copy


class ForeignKey:
    def __init__(self, spec, position):
        self.name, self.table, self.column = spec["name"], spec["table"], spec["column"]
        self.ref_table, self.ref_column = spec["ref_table"], spec["ref_column"]
        self.on_delete = spec.get("on_delete", "no_action")
        self.on_update = spec.get("on_update", "no_action")
        self.position = position


class Database:
    def __init__(self, spec):
        self.tables = spec["tables"]
        self.rows = {t: {r[d["pk"]]: dict(r) for r in spec.get("rows", {}).get(t, [])}
                     for t, d in self.tables.items()}
        self.fks = [ForeignKey(s, i) for i, s in enumerate(spec.get("foreign_keys", []))]

    def pk(self, table):
        return self.tables[table]["pk"]

    def default(self, table, column):
        return self.tables[table].get("defaults", {}).get(column)

    def referencing(self, table):
        return [fk for fk in self.fks if fk.ref_table == table]

    def children(self, fk, value):
        return [pk for pk, row in sorted(self.rows[fk.table].items()) if row.get(fk.column) == value]

    def parent_exists(self, fk, value):
        return any(r.get(fk.ref_column) == value for r in self.rows[fk.ref_table].values())

    def snapshot(self):
        return copy.deepcopy(self.rows)

    def dump(self):
        return {t: [rows[k] for k in sorted(rows)] for t, rows in self.rows.items()}
