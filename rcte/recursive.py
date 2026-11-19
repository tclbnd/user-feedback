"""Evaluation of WITH RECURSIVE queries over an edge table.

The reporting layer always generates queries of this shape:

    WITH RECURSIVE r(<columns>) AS (
        SELECT ... FROM nodes WHERE <anchor>
        UNION [ALL]
        SELECT ... FROM r JOIN edges ON edges.src = r.id
    ) [CYCLE id SET is_cycle USING path]
    SELECT <columns> FROM r

Rows carry ``id``, ``depth`` and ``path`` internally; ``columns`` decides
which of them the query actually selects.  Row order of the result follows
the iteration that produced the row.

Every evaluation records its iteration count and row count in
``graph.stats``; ``rcte.metrics`` turns those into the slow-query log the
DBA team reads.
"""
import time

ROW_LIMIT = 5_000  # work_mem budget of the reporting pool, in rows


class RowLimitExceeded(Exception):
    pass


def output_columns(query):
    columns = list(query.get("columns", ["id", "depth"]))
    if query.get("cycle"):
        columns += [c for c in ("is_cycle", "path") if c not in columns]
    return columns


def project(row, columns):
    out = {}
    for col in columns:
        out[col] = list(row[col]) if col == "path" else row[col]
    return out


def evaluate(graph, query):
    started = time.perf_counter()
    distinct = query.get("union", "all") == "distinct"
    cycle = query.get("cycle")
    iterations = 0

    work = []
    for node in graph.select(query["anchor"]):
        work.append({"id": node, "depth": 0, "path": (node,), "is_cycle": False})
    result = list(work)

    try:
        while work:
            iterations += 1
            produced = []
            seen_now = set()
            for row in work:
                if row["is_cycle"]:
                    continue
                for child in graph.children(row["id"], query.get("edge_filter")):
                    new = {"id": child, "depth": row["depth"] + 1, "path": row["path"] + (child,),
                           "is_cycle": False}
                    if cycle and child == row["id"]:
                        new["is_cycle"] = True
                    if distinct:
                        key = (new["id"], new["depth"], new["path"])
                        if key in seen_now:
                            continue
                        seen_now.add(key)
                    produced.append(new)
            result.extend(produced)
            if len(result) > ROW_LIMIT:
                raise RowLimitExceeded(f"out of memory: recursive query produced more than {ROW_LIMIT} rows")
            work = produced
    finally:
        graph.stats.record(query, iterations=iterations, rows=len(result),
                           seconds=time.perf_counter() - started)

    columns = output_columns(query)
    return [project(r, columns) for r in result]
