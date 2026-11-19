"""Streaming evaluation of the same recursive queries, for exports.

The nightly HR export walks the whole org chart (about 40k people with the
dotted-line edges included) and the MRP run explodes every top-level
product.  Materialising the full result of each iteration the way
``recursive.evaluate`` does needs several times the memory of the export
itself, so these jobs use a depth-first walk that yields rows one at a time
and keeps only the current branch on the stack.

Rows are the same dictionaries ``evaluate`` returns, but they come out in
depth-first order, which is what the CSV consumers expect (a manager is
followed by their reports).
"""
from .recursive import ROW_LIMIT, RowLimitExceeded, output_columns, project


def iterate(graph, query):
    """Yield the rows of ``query`` in depth-first order."""
    columns = output_columns(query)
    distinct = query.get("union", "all") == "distinct"
    cycle = query.get("cycle")
    edge_filter = query.get("edge_filter")

    stack = []
    for node in reversed(graph.select(query["anchor"])):
        stack.append({"id": node, "depth": 0, "path": (node,), "is_cycle": False})

    emitted = set()
    expanded = set()
    count = 0
    while stack:
        row = stack.pop()
        if distinct:
            key = (row["id"], row["depth"], row["path"])
            if key in emitted:
                continue
            emitted.add(key)
        count += 1
        if count > ROW_LIMIT:
            raise RowLimitExceeded(f"out of memory: recursive query produced more than {ROW_LIMIT} rows")
        yield project(row, columns)

        if row["is_cycle"]:
            continue
        if cycle:
            expanded.add(row["id"])
        children = graph.children(row["id"], edge_filter)
        for child in reversed(children):
            new = {"id": child, "depth": row["depth"] + 1, "path": row["path"] + (child,), "is_cycle": False}
            if cycle and child in expanded:
                new["is_cycle"] = True
            stack.append(new)


def batches(graph, query, size=500):
    """Group the streamed rows into lists of ``size`` for the bulk writers."""
    batch = []
    for row in iterate(graph, query):
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
