"""Join keys and the in-memory hash join."""

JOIN_TYPES = ("inner", "left", "right", "semi", "anti")


class JoinError(ValueError):
    pass


class MemoryExceeded(Exception):
    pass


def key_of(row, column):
    value = row.get(column)
    if isinstance(value, bool) or not (value is None or isinstance(value, (int, str))):
        raise JoinError(f"join key {column!r} must be an integer or a string, got {value!r}")
    return value


class InMemoryJoin:
    def __init__(self, kind, left_col, right_col, work_mem, emit):
        self.kind, self.left_col, self.right_col = kind, left_col, right_col
        self.work_mem, self.emit = work_mem, emit

    def run(self, build_rows, probe_rows):
        table, built = {}, []
        for row in build_rows:
            if len(built) == self.work_mem:
                raise MemoryExceeded(f"more than {self.work_mem} build rows in the hash table")
            entry = [row, False]
            built.append(entry)
            table.setdefault(key_of(row, self.right_col), []).append(entry)
        for row in probe_rows:
            self.probe(row, table.get(key_of(row, self.left_col), ()))
        for row, hit in built:
            if not hit and self.kind == "right":
                self.emit({"l": None, "r": row})

    def probe(self, row, matches):
        if self.kind == "semi":
            if matches:
                self.emit(row)
        elif self.kind == "anti":
            if not matches:
                self.emit(row)
        else:
            for entry in matches:
                entry[1] = True
                self.emit({"l": row, "r": entry[0]})
            if not matches and self.kind == "left":
                self.emit({"l": row, "r": None})
