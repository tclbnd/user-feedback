"""Ordering, peer groups and frame boundaries inside one partition."""
import functools


def _cmp_values(a, b, spec):
    if a is None or b is None:
        return (b is None) - (a is None)
    c = (a > b) - (a < b)
    return -c if spec.get("desc") else c


def compare(order_by, a, b):
    for spec in order_by:
        c = _cmp_values(a.get(spec["col"]), b.get(spec["col"]), spec)
        if c:
            return c
    return 0


def sort_partition(rows, order_by):
    return sorted(rows, key=functools.cmp_to_key(lambda a, b: compare(order_by, a, b)))


def peer_groups(rows, order_by):
    """Group index of every row; rows equal on all order keys share a group."""
    out, group = [], 0
    for i, row in enumerate(rows):
        if i and compare(order_by, rows[i - 1], row):
            group += 1
        out.append(group)
    return out


class Frames:
    """Frame boundaries of every row of one sorted partition."""

    def __init__(self, rows, order_by):
        self.rows, self.order_by, self.n = rows, order_by, len(rows)
        self.groups = peer_groups(rows, order_by)

    def bounds(self, i, frame):
        """Inclusive [start, end] row indexes of row i's frame; start > end means empty."""
        return self._edge(i, frame["start"], True), self._edge(i, frame["end"], False)

    def _edge(self, i, bound, is_start):
        kind = bound[0]
        if kind == "unbounded_preceding":
            return 0
        if kind == "unbounded_following":
            return self.n - 1
        offset = 0 if kind == "current" else bound[1] if kind == "following" else -bound[1]
        if is_start:
            return min(max(i + offset, 0), self.n)
        return min(max(i + offset, -1), self.n - 1)
