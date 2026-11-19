"""INSERT and UPDATE execution with defaults, generated columns and triggers."""
from .expr import evaluate


class DmlError(Exception):
    pass


class Session:
    def __init__(self, table):
        self.table = table
        self.sequences = {}
        self.clock = 1000
        self.audit = []

    def _default(self, spec):
        if isinstance(spec, dict) and "nextval" in spec:
            self.sequences[spec["nextval"]] = self.sequences.get(spec["nextval"], 0) + 1
            return self.sequences[spec["nextval"]]
        if isinstance(spec, dict) and "clock" in spec:
            self.clock += 1
            return self.clock
        return spec

    def _compute_generated(self, row):
        for col, expr in self.table.generated.items():
            row[col] = evaluate(expr, row)

    def _fire(self, trigger, row, event):
        for action in trigger["actions"]:
            if "set" in action:
                if action["set"] in self.table.generated:
                    raise DmlError(f"trigger {trigger['name']} cannot assign generated column {action['set']}")
                row[action["set"]] = evaluate(action["expr"], row)
            elif "log" in action:
                self.audit.append({"trigger": trigger["name"], "event": event,
                                   "row": {c: row.get(c) for c in action["log"]}, "count": len(self.table.rows)})

    def _check(self, row):
        for col in self.table.not_null:
            if row.get(col) is None:
                raise DmlError(f"null value in column {col}")

    def insert(self, rows):
        t = self.table
        defaults = {c: self._default(spec) for c, spec in t.defaults.items()}
        stored = []
        for values in rows:
            row = {c: values.get(c, defaults.get(c)) for c in t.columns if c not in t.generated}
            self._compute_generated(row)
            for trig in t.triggers_for("before", "insert"):
                self._fire(trig, row, "insert")
            self._check(row)
            if row[t.key] in t.rows:
                raise DmlError(f"duplicate key {row[t.key]}")
            t.rows[row[t.key]] = row
            stored.append(row)
            for trig in t.triggers_for("after", "insert"):
                self._fire(trig, dict(row), "insert")
        return [dict(r) for r in stored]

    def update(self, key_values, changes):
        t = self.table
        out = []
        for key in key_values:
            old = t.rows[key]
            row = dict(old)
            row.update({c: evaluate(e, old) for c, e in changes.items()})
            for trig in t.triggers_for("before", "update"):
                self._fire(trig, row, "update")
            self._check(row)
            t.rows[key] = row
            out.append(row)
        for row in out:
            self._compute_generated(row)
            for trig in t.triggers_for("after", "update"):
                self._fire(trig, dict(row), "update")
        return [dict(r) for r in out]

    def upsert(self, rows, conflict_set):
        """INSERT ... ON CONFLICT (key) DO UPDATE SET <conflict_set>."""
        t = self.table
        defaults = {c: self._default(spec) for c, spec in t.defaults.items()}
        out = []
        for values in rows:
            row = {c: values.get(c, defaults.get(c)) for c in t.columns if c not in t.generated}
            key = row[t.key]
            if key in t.rows:
                old = t.rows[key]
                new = dict(old)
                ctx = dict(old, excluded=row)
                new.update({c: evaluate(e, ctx) for c, e in conflict_set.items()})
                self._compute_generated(new)
                for trig in t.triggers_for("before", "update"):
                    self._fire(trig, new, "update")
                self._check(new)
                t.rows[key] = new
                for trig in t.triggers_for("after", "insert"):
                    self._fire(trig, dict(new), "update")
                out.append(new)
                continue
            self._compute_generated(row)
            for trig in t.triggers_for("before", "insert"):
                self._fire(trig, row, "insert")
            self._check(row)
            t.rows[key] = row
            for trig in t.triggers_for("after", "insert"):
                self._fire(trig, dict(row), "insert")
            out.append(row)
        return [dict(r) for r in out]
