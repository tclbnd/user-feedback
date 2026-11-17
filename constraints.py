"""Unique constraints, their checking modes and conflict lookup."""


class Violation(Exception):
    def __init__(self, code, constraint=None, key=None, row=None, at="statement"):
        super().__init__(code)
        self.info = {"code": code, "constraint": constraint,
                     "key": list(key) if key is not None else None, "row": row, "at": at}


class Constraint:
    def __init__(self, spec):
        self.name, self.columns = spec["name"], spec["columns"]
        self.deferrable = spec.get("deferrable", False)
        self.initially = spec.get("initially", "immediate")

    def key(self, row):
        values = tuple(row.get(c) for c in self.columns)
        return None if None in values else values


class Constraints:
    def __init__(self, specs):
        self.all = [Constraint(s) for s in specs]
        self.reset()

    def reset(self):
        self.mode = {c.name: c.initially for c in self.all}

    def get(self, name):
        return next((c for c in self.all if c.name == name), None)

    def in_mode(self, mode):
        return [c for c in self.all if self.mode[c.name] == mode]

    def find_conflict(self, rows, row, target):
        order = sorted(self.all, key=lambda c: c.name != target)
        for c in order:
            key = c.key(row)
            rid = next((rid for rid, other in rows.items() if key is not None and c.key(other) == key), None)
            if rid is not None:
                return c, rid
        return None

    def check(self, rows, constraints, at):
        for c in constraints:
            keys = [c.key(r) for _, r in sorted(rows.items()) if c.key(r) is not None]
            dups = sorted({k for k in keys if keys.count(k) > 1}, key=repr)
            if dups:
                raise Violation("unique_violation", c.name, dups[0], None, at)
