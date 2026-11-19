"""Prepared statements.

The order API sends its queries as prepared statements: constants in the
WHERE clause are parameters, written ``{"param": name}`` in place of a
constant, and every execution supplies new values.  Choosing the plan is
the expensive part, so a statement is planned on its first execution and
that plan (the chosen partial index or the full scan) is reused for every
later execution.
"""
from .planner import choose
from .predicates import truth


def bind(pred, params):
    """Replace parameter references with the values of one execution."""
    if isinstance(pred, dict):
        if set(pred) == {"param"}:
            return params[pred["param"]]
        return {k: bind(v, params) for k, v in pred.items()}
    if isinstance(pred, list):
        return [bind(v, params) for v in pred]
    return pred


class PreparedQuery:
    def __init__(self, query, indexes):
        self.query = query
        self.indexes = indexes
        self.plan = None
        self.plannings = 0

    def execute(self, params, rows):
        bound = bind(self.query, params)
        if self.plan is None:
            ix = choose(bound, self.indexes)
            self.plan = ix if ix else "seq scan"
            self.plannings += 1
        source = rows if self.plan == "seq scan" else self.plan.rows
        out = sorted(r["id"] for r in source if truth(bound, r) is True)
        name = self.plan if self.plan == "seq scan" else self.plan.name
        return {"plan": name, "ids": out, "rows_read": len(source)}
