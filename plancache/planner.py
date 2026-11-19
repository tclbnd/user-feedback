"""Cost-based planner for the two-table join queries the API issues.

A query joins ``outer`` to ``inner`` on ``inner.<join_col>`` and may filter
the outer table on ``outer.<filter_col> = $1``.
"""

NESTED_LOOP_LIMIT = 2_000   # estimated outer*inner below which a nested loop is cheapest


class Plan:
    def __init__(self, join, inner_index=None, outer_index=None, estimate=0):
        self.join = join
        self.inner_index = inner_index
        self.outer_index = outer_index
        self.estimate = estimate

    def describe(self):
        parts = [self.join]
        if self.outer_index:
            parts.append(f"outer via {self.outer_index}")
        if self.inner_index:
            parts.append(f"inner via {self.inner_index}")
        return ", ".join(parts)


def plan(query, catalog):
    outer = catalog.tables[query["outer"]]
    inner = catalog.tables[query["inner"]]
    outer_index = catalog.index_on(outer.name, query.get("filter_col")) if query.get("filter_col") else None
    inner_index = catalog.index_on(inner.name, query["join_col"])
    outer_rows = max(1, outer.stats_rows // 100) if outer_index else max(1, outer.stats_rows)
    if inner_index:
        return Plan("nested loop", inner_index, outer_index, outer_rows * 2)
    if outer_rows * inner.stats_rows <= NESTED_LOOP_LIMIT:
        return Plan("nested loop", None, outer_index, outer_rows * inner.stats_rows)
    return Plan("hash join", None, outer_index, outer_rows + inner.stats_rows)
