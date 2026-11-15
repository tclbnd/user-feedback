"""Adjacency access used by the local graph auditor."""

from collections import defaultdict


class EdgeStore:
    def __init__(self):
        self._out = defaultdict(set)
        self._in = defaultdict(set)
        self._res = defaultdict(set)
        self.edges_visited = 0

    def begin_batch(self, version):
        self.edges_visited = 0

    def add(self, source, kind, target):
        self._out[source].add((kind, target))
        if kind == "res":
            self._res[target].add((kind, source))
        else:
            self._in[target].add((kind, source))

    def remove_page(self, source):
        for kind, target in self._out.pop(source, ()):
            index = self._res if kind == "res" else self._in
            index[target].discard((kind, source))
            if not index[target]:
                del index[target]

    def outgoing(self, page):
        edges = self._out.get(page, ())
        self.edges_visited += len(edges)
        return tuple(sorted(edges))

    def incoming(self, page):
        edges = self._in.get(page, ())
        self.edges_visited += len(edges)
        return tuple(sorted(edges))

    def resource_users(self, resource):
        edges = self._res.get(resource, ())
        self.edges_visited += len(edges)
        return tuple(sorted(edges))

    def export(self):
        return {
            name: {key: [list(edge) for edge in sorted(edges)]
                   for key, edges in sorted(index.items()) if edges}
            for name, index in (
                ("outbound", self._out), ("inbound", self._in), ("resource_users", self._res)
            )
        }
