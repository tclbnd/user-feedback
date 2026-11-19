"""Per-graph query statistics and the slow-query log.

The DBA team alerts on recursive queries that need many iterations or come
close to the row budget; ``slow_log`` produces the lines they collect.
"""
import json


class QueryStats:
    def __init__(self):
        self.entries = []

    def record(self, query, iterations, rows, seconds):
        self.entries.append({
            "anchor": query.get("anchor"),
            "union": query.get("union", "all"),
            "cycle": bool(query.get("cycle")),
            "iterations": iterations,
            "rows": rows,
            "ms": round(seconds * 1000, 2),
        })

    def last(self):
        return self.entries[-1] if self.entries else None


def slow_log(stats, min_iterations=8, min_rows=2_000):
    lines = []
    for e in stats.entries:
        if e["iterations"] >= min_iterations or e["rows"] >= min_rows:
            lines.append("recursive-query " + json.dumps(e, sort_keys=True))
    return lines
