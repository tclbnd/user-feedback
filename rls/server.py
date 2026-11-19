"""The query service: one process, one catalog, many tenant sessions.

Requests from different tenants arrive interleaved on the same worker;
each request runs with the tenant of its session.
"""
from .catalog import QueryError
from .executor import Executor
from .planner import rewrite


def serve(catalog, requests):
    """``requests`` is a list of {"tenant": ..., "plan": ...}; returns one result per request."""
    results = []
    for req in requests:
        ex = Executor(catalog)
        try:
            rows = ex.run(rewrite(req["plan"], catalog, req["tenant"]))
            res = {"tenant": req["tenant"], "ids": sorted(r["id"] for r in rows)}
        except QueryError as exc:
            res = {"tenant": req["tenant"], "error": str(exc)}
        res.update(rows_scanned=ex.rows_scanned, subplan_runs=ex.subplan_runs)
        results.append(res)
    return results
