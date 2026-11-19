"""Path-value index over a JSON document column.

Every scalar leaf of a document is recorded under a normalised path (array
positions dropped) together with its value, so ``$.tags[*] = "red"`` and
``$.a.b = 1`` become single index probes.
"""
from collections import defaultdict

from .path import RANGE, parse


def _norm(steps):
    return "$" + "".join(f".{arg}" for kind, arg in steps if kind == "key")


def _leaves(value, prefix):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, prefix + f".{k}")
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v, prefix)
    elif value is not None:
        yield prefix, value


class PathIndex:
    def __init__(self):
        self.values = defaultdict(set)    # (path, str(value)) -> doc ids
        self.paths = defaultdict(set)     # path -> doc ids having a value there

    def add(self, doc_id, doc):
        for path, value in _leaves(doc, "$"):
            self.values[(path, str(value))].add(doc_id)
            self.paths[path].add(doc_id)

    def remove(self, doc_id):
        for key in list(self.values):
            self.values[key].discard(doc_id)
        for path in list(self.paths):
            self.paths[path].discard(doc_id)

    def supports(self, pred):
        return pred["op"] in ("=", "exists", "is_null") or pred["op"] in RANGE

    def lookup(self, pred):
        path = _norm(parse(pred["path"]))
        if pred["op"] == "=":
            return set(self.values.get((path, str(pred["value"])), ()))
        if pred["op"] == "exists":
            return set(self.paths.get(path, ()))
        if pred["op"] == "is_null":
            return set(self.values.get((path, "None"), ()))
        if pred["op"] in RANGE:
            # range scan over the value entries of this path
            want = str(pred["value"])
            out = set()
            for (p, v), ids in self.values.items():
                if p == path and RANGE[pred["op"]](v, want):
                    out |= ids
            return out
        raise ValueError(pred["op"])
