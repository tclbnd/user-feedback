"""Executes rewritten plans.

Scan nodes return the table's rows; filters keep rows whose predicate is
true.  The RLS qual of a scan is evaluated after the user's filters above
it so cheap user predicates can discard rows first.
"""
import operator

OPS = {"=": operator.eq, "!=": operator.ne, "<": operator.lt, ">": operator.gt}


class Executor:
    def __init__(self, catalog):
        self.catalog = catalog
        self.rows_scanned = 0
        self.subplan_runs = 0

    def run(self, rewritten):
        node = rewritten["secure"]
        rows, rls = self._exec(node)
        return [r for r in rows if all(self._pred(q, r, None) for q in rls)]

    def _exec(self, node):
        if "scan" in node:
            rows = list(self.catalog.tables[node["scan"]])
            self.rows_scanned += len(rows)
            return rows, [node["rls"]] if node["rls"] else []
        if "filter" in node:
            rows, rls = self._exec(node["input"])
            return [r for r in rows if self._pred(node["filter"], r, None)], rls
        raise ValueError(node)

    def _value(self, term, row, outer):
        if isinstance(term, str) and term.startswith("outer."):
            return outer[term[6:]]
        if isinstance(term, str) and term.startswith("col."):
            return row[term[4:]]
        return term

    def _pred(self, pred, row, outer):
        if "and" in pred:
            return all(self._pred(p, row, outer) for p in pred["and"])
        if "cmp" in pred:
            col, op, val = pred["cmp"]
            return OPS[op](row.get(col), self._value(val, row, outer))
        if "fn" in pred:
            _, fn = self.catalog.functions[pred["fn"]]
            return bool(fn(*[self._value(a, row, outer) for a in pred["args"]]))
        if "exists" in pred:
            self.subplan_runs += 1
            sub = pred["exists"]
            inner_rows, rls = self._exec(sub["plan"])
            outer_col, inner_col = sub["corr"]
            return any(r[inner_col] == row[outer_col] and all(self._pred(q, r, None) for q in rls)
                       for r in inner_rows)
        raise ValueError(pred)
