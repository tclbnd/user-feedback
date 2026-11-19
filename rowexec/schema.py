"""Table definitions: columns with defaults, stored generated columns and
row triggers.

A default is a literal, ``{"nextval": seq}`` or ``{"clock": true}`` (the
server clock, which advances by one for every value it hands out).
A trigger is ``{"name": ..., "timing": "before" | "after",
"events": ["insert", "update"], "actions": [...]}`` with actions
``{"set": col, "expr": e}`` (BEFORE only: change NEW) and
``{"log": [cols...]}`` (append NEW's columns and the table's current row
count to the audit log).
"""


class Table:
    def __init__(self, spec):
        self.name = spec["name"]
        self.key = spec["key"]
        self.columns = [c["name"] for c in spec["columns"]]
        self.defaults = {c["name"]: c["default"] for c in spec["columns"] if "default" in c}
        self.generated = {c["name"]: c["generated"] for c in spec["columns"] if "generated" in c}
        self.not_null = {c["name"] for c in spec["columns"] if c.get("not_null")}
        self.triggers = {}
        for t in spec.get("triggers", []):
            self.triggers[t["name"]] = t
        self.rows = {}

    def triggers_for(self, timing, event):
        names = {n for n, t in self.triggers.items() if t["timing"] == timing and event in t["events"]}
        return [self.triggers[n] for n in names]
