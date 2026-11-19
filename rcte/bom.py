"""Bill-of-materials helpers built on recursive queries."""
from collections import Counter

from .recursive import evaluate
from .stream import iterate

# The MRP run guards every explosion with a CYCLE clause: a parts list that
# loops back on itself is a data-entry error and must not hang the run.
MRP_QUERY = {"union": "all", "columns": ["id", "path"], "cycle": ["id"]}


def _units(path, qty):
    units = 1
    for a, b in zip(path, path[1:]):
        units *= qty(a, b)
    return units


def explode(graph, product, quantities=None):
    """Total quantity of every component needed for one ``product``.

    ``quantities`` maps (parent, child) -> units of child per parent and
    overrides the ``qty`` stored on the edges.
    """
    def qty(a, b):
        if quantities is not None and (a, b) in quantities:
            return quantities[(a, b)]
        return graph.edge_qty(a, b)

    rows = evaluate(graph, {"anchor": {"id": product}, "union": "all", "columns": ["id", "path"]})
    need = Counter()
    for row in rows:
        path = row["path"]
        if len(path) < 2:
            continue
        need[row["id"]] += _units(path, qty)
    return dict(sorted(need.items()))


def mrp_requirements(graph, product, units=1):
    """Gross requirements for ``units`` of ``product``, as used by the MRP run.

    Streams the explosion so that products with very deep structures do not
    have to be materialised; loop rows flagged by the CYCLE clause are
    reported separately instead of being counted.
    """
    need = Counter()
    loops = []
    for row in iterate(graph, dict(MRP_QUERY, anchor={"id": product})):
        path = row["path"]
        if row["is_cycle"]:
            loops.append(path)
            continue
        if len(path) < 2:
            continue
        need[row["id"]] += units * _units(path, graph.edge_qty)
    return {"requirements": dict(sorted(need.items())), "loops": loops}


def levels(graph, product):
    rows = evaluate(graph, {"anchor": {"id": product}, "union": "distinct", "columns": ["id", "depth"]})
    out = {}
    for row in rows:
        out.setdefault(row["depth"], []).append(row["id"])
    return out
