"""Decorrelated subqueries: the inner side is filtered, grouped and aggregated once."""
from engine import and3


def refs(node):
    """Aliases an operand or predicate reads."""
    if node.get("col"):
        return {node["col"].split(".", 1)[0]}
    if "scalar" in node:
        return refs({"args": node["scalar"].get("where", [])}) - {node["scalar"]["as"]}
    out = set()
    for key in ("left", "right", "arg"):
        if key in node:
            out |= refs(node[key])
    for p in node.get("args", []):
        out |= refs(p)
    if "query" in node:
        out |= refs({"args": node["query"].get("where", [])}) - {node["query"]["as"]}
    return out


def fold(sub, envs):
    agg, col, alias = sub.get("agg"), sub.get("col"), sub["as"]
    values = [e[alias].get(col.split(".", 1)[1]) if col else 1 for e in envs]
    if agg is None:
        return values[0]
    values = [v for v in values if v is not None]
    if agg == "count":
        return len(values)
    return {"sum": sum, "min": min, "max": max}[agg](values) if values else None


class Decorrelated:
    def __init__(self, sub, ctx, outer, exists=False):
        self.sub, self.ctx, self.exists = sub, ctx, exists
        self.local, self.keys = [], []
        for p in sub.get("where", []):
            if not refs(p) & outer:
                self.local.append(p)
            elif p["op"] == "=" and refs(p["right"]) <= outer and not refs(p["left"]) & outer:
                self.keys.append((p["left"], p["right"]))
            elif p["op"] == "=" and refs(p["left"]) <= outer and not refs(p["right"]) & outer:
                self.keys.append((p["right"], p["left"]))

    def prepare(self):
        sub, ctx, alias = self.sub, self.ctx, self.sub["as"]
        groups = {}
        for row in ctx.db[sub["from"]]["rows"]:
            env = {alias: row}
            if and3(ctx.truth(p, env) for p in self.local) is True:
                key = tuple(ctx.operand(inner, env) for inner, _ in self.keys)
                groups.setdefault(key, []).append(env)
        self.values = {k: (True if self.exists else fold(sub, envs)) for k, envs in groups.items()}

    def value(self, env):
        key = tuple(self.ctx.operand(outer, env) for _, outer in self.keys)
        if None in key or key not in self.values:
            return False if self.exists else None
        return self.values[key]
