"""Edge tables for the org chart and the bill of materials.

Org chart edges have ``kind`` ``manages`` (solid line) or ``dotted``
(matrix reporting).  BOM edges carry ``qty``, the number of child units per
parent unit.
"""
from collections import defaultdict

from .metrics import QueryStats


class Graph:
    def __init__(self, nodes, edges):
        self.nodes = {n["id"]: n for n in nodes}
        self.edges = list(edges)
        self.out = defaultdict(list)
        for e in self.edges:
            self.out[e["src"]].append(e)
        self.stats = QueryStats()

    def select(self, anchor):
        """Anchor rows: nodes whose attributes match every key in ``anchor``."""
        return [nid for nid, n in sorted(self.nodes.items()) if all(n.get(k) == v for k, v in anchor.items())]

    def children(self, node, edge_filter=None):
        out = []
        for e in self.out.get(node, []):
            if edge_filter and any(e.get(k) != v for k, v in edge_filter.items()):
                continue
            out.append(e["dst"])
        return out

    def edge_qty(self, src, dst):
        """Units of ``dst`` per unit of ``src`` (1 when the edge has no ``qty``)."""
        for e in self.out.get(src, []):
            if e["dst"] == dst:
                return e.get("qty", 1)
        raise KeyError((src, dst))


def load(spec):
    return Graph(spec["nodes"], spec["edges"])
