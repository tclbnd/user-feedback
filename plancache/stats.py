"""Per-statement counters behind the ``pg_prepared_statements``-style view.

The on-call dashboard shows, for every prepared statement, how often it
ran, how often it had to be planned and the share of executions served by
a cached plan.  ``hit_ratio`` below 0.9 on a hot statement pages the DBA.
"""


def statement_view(server):
    rows = []
    for name, stmt in sorted(server.statements.items()):
        hits = stmt.executions - stmt.plannings
        rows.append({
            "name": name,
            "executions": stmt.executions,
            "plannings": stmt.plannings,
            "hit_ratio": round(hits / stmt.executions, 3) if stmt.executions else None,
            "plan": stmt.plan.describe() if stmt.plan else None,
        })
    return rows
