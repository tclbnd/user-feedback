"""Three-valued evaluation of plain predicates and subquery predicates."""

CMP = {"=": lambda a, b: a == b, "<>": lambda a, b: a != b, "<": lambda a, b: a < b,
       "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}
SUBQUERY_OPS = ("in", "not_in", "exists", "not_exists")


def value(node, env):
    if "value" in node:
        return node["value"]
    alias, column = node["col"].split(".", 1)
    return env[alias].get(column)


def and3(values):
    values = list(values)
    if False in values:
        return False
    return None if None in values else True


def not3(v):
    return None if v is None else not v


def plain(pred, env):
    op = pred["op"]
    if op in ("is_null", "is_not_null"):
        return (value(pred["arg"], env) is None) == (op == "is_null")
    a, b = value(pred["left"], env), value(pred["right"], env)
    if a is None or b is None:
        return None
    return CMP[op](a, b)


def subquery_rows(sub, db, env):
    """Rows of a (possibly correlated) subquery for the outer row bound in env."""
    out = []
    for row in db[sub["from"]]["rows"]:
        inner = dict(env, **{sub["as"]: row})
        if and3(plain(p, inner) for p in sub.get("where", [])) is True:
            out.append(inner)
    return out


def row_compare(args, select, outer_env, inner_env):
    return and3(_eq(value(a, outer_env), value({"col": c}, inner_env)) for a, c in zip(args, select))


def _eq(a, b):
    return None if a is None or b is None else a == b


def in3(args, select, env, rows):
    comps = [row_compare(args, select, env, inner) for inner in rows]
    if True in comps:
        return True
    return None if None in comps else False


def evaluate(pred, db, env):
    op = pred["op"]
    if op not in SUBQUERY_OPS:
        return plain(pred, env)
    rows = subquery_rows(pred["query"], db, env)
    if op == "exists":
        return bool(rows)
    if op == "not_exists":
        return not rows
    result = in3(pred["args"], pred["query"]["select"], env, rows)
    return result if op == "in" else not3(result)
