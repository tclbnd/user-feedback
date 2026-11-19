"""Expressions used by defaults, generated columns and trigger actions.

    literal | {"col": name} | {"excluded": name} | {"add": [a, b]} | {"mul": [a, b]} |
    {"concat": [a, b, ...]} | {"upper": a} | {"round": [a, digits]}

NULL (None) propagates through arithmetic and concat.  ``{"excluded": name}``
is only meaningful in the SET list of ON CONFLICT DO UPDATE, where the row
being evaluated carries the proposed row under the key ``"excluded"``.
"""


def evaluate(expr, row):
    if not isinstance(expr, dict):
        return expr
    if "col" in expr:
        return row.get(expr["col"])
    if "excluded" in expr:
        return (row.get("excluded") or {}).get(expr["excluded"])
    if "upper" in expr:
        v = evaluate(expr["upper"], row)
        return None if v is None else str(v).upper()
    if "round" in expr:
        v, d = evaluate(expr["round"][0], row), expr["round"][1]
        return None if v is None else round(v, d)
    key = next(iter(expr))
    vals = [evaluate(e, row) for e in expr[key]]
    if any(v is None for v in vals):
        return None
    if key == "add":
        return vals[0] + vals[1]
    if key == "mul":
        return vals[0] * vals[1]
    if key == "concat":
        return "".join(str(v) for v in vals)
    raise ValueError(f"bad expression {expr}")
