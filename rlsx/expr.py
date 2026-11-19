"""Policy expressions over a row and the session.

  {"cmp": [col, op, value]}   value may be {"session": "user"} / {"session": "tenant"}
  {"and": [...]}  {"or": [...]}  {"not": e}  true / false
"""
import operator

OPS = {"=": operator.eq, "<>": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def value(v, session):
    if isinstance(v, dict) and "session" in v:
        return session.get(v["session"])
    return v


def evaluate(e, row, session):
    if e is True or e is False:
        return e
    if "and" in e:
        return all(evaluate(x, row, session) for x in e["and"])
    if "or" in e:
        return any(evaluate(x, row, session) for x in e["or"])
    if "not" in e:
        return not evaluate(e["not"], row, session)
    col, op, v = e["cmp"]
    a, b = row.get(col), value(v, session)
    if a is None or b is None:
        return False
    return OPS[op](a, b)
