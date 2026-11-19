"""In-memory table with a primary key and optional unique columns."""
import copy


class ConstraintError(Exception):
    pass


class Table:
    def __init__(self, name, key, columns, unique=()):
        self.name = name
        self.key = key
        self.columns = list(columns)
        self.unique = tuple(unique)
        self.rows = {}
        self.next_rowid = 1

    def insert(self, values):
        row = {c: values.get(c) for c in self.columns}
        if row[self.key] is None:
            raise ConstraintError(f"{self.name}.{self.key} cannot be null")
        if any(r[self.key] == row[self.key] for r in self.rows.values()):
            raise ConstraintError(f"duplicate key {self.key}={row[self.key]} in {self.name}")
        for col in self.unique:
            if row[col] is not None and any(r[col] == row[col] for r in self.rows.values()):
                raise ConstraintError(f"duplicate value {col}={row[col]} in {self.name}")
        rowid = self.next_rowid
        self.next_rowid += 1
        self.rows[rowid] = row
        return rowid

    def update(self, rowid, changes):
        row = dict(self.rows[rowid], **changes)
        for col in self.unique:
            if row[col] is not None and any(r[col] == row[col] for i, r in self.rows.items() if i != rowid):
                raise ConstraintError(f"duplicate value {col}={row[col]} in {self.name}")
        self.rows[rowid] = row

    def delete(self, rowid):
        del self.rows[rowid]

    def find(self, col, value):
        return [i for i, r in self.rows.items() if r[col] == value]

    def snapshot(self):
        return copy.deepcopy((self.rows, self.next_rowid))

    def restore(self, snap):
        self.rows, self.next_rowid = copy.deepcopy(snap)

    def dump(self):
        return sorted(self.rows.values(), key=lambda r: (r[self.key] is None, str(r[self.key])))
