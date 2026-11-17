"""Row-by-row evaluation: three-valued predicates, aggregates and SubPlans."""

OPS = {"=": lambda a, b: a == b, "<>": lambda a, b: a != b, "<": lambda a, b: a < b,
       "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


class MultipleRows(Exception):
    pass


def and3(values):
    values = list(values)
    return False if False in values else (None if None in values else True)


class Context:
    def __init__(self, db):
        self.db, self.resolvers, self.subplan_runs = db, {}, 0

    def operand(self, node, env):
        if "value" in node:
            return node["value"]
        if "scalar" in node:
            return self.resolvers[id(node["scalar"])].value(env)
        alias, col = node["col"].split(".", 1)
        return env[alias].get(col)

    def compare(self, op, a, b):
        return None if a is None or b is None else OPS[op](a, b)

    def truth(self, p, env):
        op = p["op"]
        if op in ("exists", "not_exists"):
            found = self.resolvers[id(p["query"])].value(env)
            return found if op == "exists" else not found
        if op == "or":
            values = [self.truth(q, env) for q in p["args"]]
            return True if True in values else (None if None in values else False)
        if op in ("is_null", "is_not_null"):
            return (self.operand(p["arg"], env) is None) == (op == "is_null")
        return self.compare(op, self.operand(p["left"], env), self.operand(p["right"], env))


def aggregate(sub, envs, exists=False):
    if exists:
        return bool(envs)
    agg, col = sub.get("agg"), sub.get("col")
    alias = sub["as"]
    values = [e[alias].get(col.split(".", 1)[1]) if col else 1 for e in envs]
    if agg is None:
        if len(values) > 1:
            raise MultipleRows("more than one row returned by a subquery used as an expression")
        return values[0] if values else None
    values = [v for v in values if v is not None]
    if agg == "count":
        return len(values)
    if not values:
        return None
    return {"sum": sum, "min": min, "max": max}[agg](values)


class SubPlan:
    def __init__(self, sub, ctx, exists=False):
        self.sub, self.ctx, self.exists = sub, ctx, exists

    def prepare(self):
        pass

    def value(self, env):
        self.ctx.subplan_runs += 1
        sub, out = self.sub, []
        for row in self.ctx.db[sub["from"]]["rows"]:
            inner = dict(env, **{sub["as"]: row})
            if and3(self.ctx.truth(p, inner) for p in sub.get("where", [])) is True:
                out.append(inner)
        return aggregate(sub, out, self.exists)
