"""Exports used by the HR reporting job.

``org_rows`` is the nightly reporting-line export: everybody below the CEO
with their depth and the chain of managers, solid and dotted lines alike.
``people_under`` feeds the quarterly access review, which needs each
person below a manager exactly once.
"""
import csv
import io

from .stream import batches, iterate


def org_rows(graph, top=None):
    anchor = {"id": top} if top else {"title": "CEO"}
    query = {"anchor": anchor, "union": "all", "columns": ["id", "depth", "path"], "cycle": ["id"]}
    for batch in batches(graph, query):
        yield from batch


def people_under(graph, manager, kind=None):
    query = {"anchor": {"id": manager}, "union": "distinct", "columns": ["id"]}
    if kind:
        query["edge_filter"] = {"kind": kind}
    return [row["id"] for row in iterate(graph, query) if row["id"] != manager]


def to_csv(rows):
    rows = list(rows)
    if not rows:
        return ""
    buf = io.StringIO()
    cols = list(rows[0])
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(cols)
    for row in rows:
        writer.writerow(["/".join(row[c]) if isinstance(row[c], list) else row[c] for c in cols])
    return buf.getvalue()
