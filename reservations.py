"""Incremental lease bookkeeping for the offline backfill reservation tool.

The module deliberately has no dependency on wall-clock time: all state is
derived from the ordered event stream.  ``process(events)`` replays a already
validated, seq-ordered event list and returns the public result document.

Per key there is an augmented treap keyed by ``(start, lease)``.  Every node
stores the maximum ``end`` and the minimum ``start`` of its subtree, which lets
an overlap query prune whole subtrees instead of scanning every live lease.  A
min-heap provides the expiry index; expired and released leases are removed
from the treap when they are popped, while stale heap entries are skipped
lazily.
"""

import heapq
import random
import sys

# Treap operations are recursive.  Random priorities keep the expected depth
# logarithmic even for the 10k-interval workload; be generous with the limit.
if sys.getrecursionlimit() < 200000:
    sys.setrecursionlimit(200000)


class _Node:
    __slots__ = (
        "start",
        "end",
        "lease",
        "priority",
        "left",
        "right",
        "max_end",
        "min_start",
    )

    def __init__(self, start, end, lease, priority):
        self.start = start
        self.end = end
        self.lease = lease
        self.priority = priority
        self.left = None
        self.right = None
        self.max_end = end
        self.min_start = start


def _update(node):
    max_end = node.end
    min_start = node.start
    left = node.left
    if left is not None:
        if left.max_end > max_end:
            max_end = left.max_end
        if left.min_start < min_start:
            min_start = left.min_start
    right = node.right
    if right is not None:
        if right.max_end > max_end:
            max_end = right.max_end
        if right.min_start < min_start:
            min_start = right.min_start
    node.max_end = max_end
    node.min_start = min_start


def _rotate_right(root):
    child = root.left
    root.left = child.right
    child.right = root
    _update(root)
    _update(child)
    return child


def _rotate_left(root):
    child = root.right
    root.right = child.left
    child.left = root
    _update(root)
    _update(child)
    return child


def _merge(left, right):
    if left is None:
        return right
    if right is None:
        return left
    if left.priority < right.priority:
        left.right = _merge(left.right, right)
        _update(left)
        return left
    right.left = _merge(left, right.left)
    _update(right)
    return right


def _erase(root, start, lease):
    if root is None:
        return None
    node_key = (root.start, root.lease)
    target = (start, lease)
    if target == node_key:
        return _merge(root.left, root.right)
    if target < node_key:
        root.left = _erase(root.left, start, lease)
    else:
        root.right = _erase(root.right, start, lease)
    _update(root)
    return root


class IntervalIndex:
    """Treap interval index for one key, augmented with max_end/min_start."""

    def __init__(self):
        self.root = None
        self.checks = 0
        # A fixed seed keeps overlap_checks deterministic for identical input
        # regardless of the order in which the caller supplied the events.
        self._rng = random.Random(0x51A7C0DE)

    def insert(self, start, end, lease):
        node = _Node(start, end, lease, self._rng.getrandbits(64))
        self.root = self._insert(self.root, node)

    def _insert(self, root, node):
        if root is None:
            return node
        node_key = (node.start, node.lease)
        root_key = (root.start, root.lease)
        if node_key < root_key:
            root.left = self._insert(root.left, node)
            if root.left.priority < root.priority:
                root = _rotate_right(root)
        else:
            root.right = self._insert(root.right, node)
            if root.right.priority < root.priority:
                root = _rotate_left(root)
        _update(root)
        return root

    def remove(self, start, lease):
        self.root = _erase(self.root, start, lease)

    def __bool__(self):
        return self.root is not None

    def query(self, start, end):
        """Return (overlapping lease ids, number of index nodes visited)."""
        found = []
        self.checks = 0
        self._query(self.root, start, end, found)
        return found, self.checks

    def _query(self, node, start, end, found):
        if node is None:
            return
        self.checks += 1

        left = node.left
        if left is not None and left.max_end > start and left.min_start < end:
            self._query(left, start, end, found)

        if node.start < end and start < node.end:
            found.append(node.lease)

        right = node.right
        if (
            right is not None
            and node.start < end
            and right.max_end > start
            and right.min_start < end
        ):
            self._query(right, start, end, found)


def _lease_view(info):
    return {
        "key": info["key"],
        "lease": info["lease"],
        "epoch": info["epoch"],
        "start": info["start"],
        "end": info["end"],
        "expires": info["expires"],
    }


def _sort_key(item):
    return item["key"], item["lease"]


class ReplayState:
    """Mutable state produced by replaying committed journal events."""

    def __init__(self):
        self.active = {}
        self.published = []
        self.published_ids = set()
        self.epochs = {}
        self.indexes = {}
        self.expiry = []
        self.overlap_checks = 0

    # -- interval/expiry bookkeeping -----------------------------------
    def _expire_until(self, now):
        heap = self.expiry
        while heap and heap[0][0] <= now:
            expires, lease = heapq.heappop(heap)
            info = self.active.get(lease)
            if info is None:
                # Released leases leave a lazy heap entry behind.
                continue
            if info["expires"] != expires:
                continue
            self._drop_active(lease)

    def _drop_active(self, lease):
        info = self.active.pop(lease)
        index = self.indexes.get(info["key"])
        if index is not None:
            index.remove(info["start"], lease)
            if not index:
                del self.indexes[info["key"]]

    # -- event handlers -------------------------------------------------
    def _reserve(self, event):
        key = event["key"]
        start = event["start"]
        end = event["end"]
        expires = event["at"] + event["ttl"]

        index = self.indexes.get(key)
        blockers = []
        if index is not None:
            overlaps, checks = index.query(start, end)
            self.overlap_checks += checks
            if overlaps:
                blockers = sorted(overlaps)

        if blockers:
            return {"seq": event["seq"], "status": "conflict", "blockers": blockers}

        epoch = self.epochs.get(key, 0) + 1
        self.epochs[key] = epoch
        info = {
            "key": key,
            "lease": event["seq"],
            "epoch": epoch,
            "start": start,
            "end": end,
            "expires": expires,
        }
        self.active[info["lease"]] = info
        heapq.heappush(self.expiry, (expires, info["lease"]))
        if index is None:
            index = IntervalIndex()
            self.indexes[key] = index
        index.insert(start, end, info["lease"])
        return {
            "seq": event["seq"],
            "status": "reserved",
            "lease": info["lease"],
            "epoch": epoch,
        }

    def _publish_or_release(self, event):
        lease_id = event["lease"]
        info = self.active.get(lease_id)
        if info is None or info["key"] != event["key"] or info["epoch"] != event["epoch"]:
            return {"seq": event["seq"], "status": "stale"}

        if event["op"] == "release":
            self._drop_active(lease_id)
            return {"seq": event["seq"], "status": "released"}

        if lease_id in self.published_ids:
            return {"seq": event["seq"], "status": "already_published"}
        self.published_ids.add(lease_id)
        self.published.append(_lease_view(info))
        return {"seq": event["seq"], "status": "published"}

    def apply(self, event):
        # Expired leases are never live for the event being applied.
        self._expire_until(event["at"])
        op = event["op"]
        if op == "reserve":
            return self._reserve(event)
        if op in ("publish", "release"):
            return self._publish_or_release(event)
        # tick: logical time advanced through the cleanup above.
        return {"seq": event["seq"], "status": "tick"}

    def outcome(self, results):
        active = sorted((_lease_view(info) for info in self.active.values()), key=_sort_key)
        published = sorted((dict(entry) for entry in self.published), key=_sort_key)
        return {
            "results": results,
            "active": active,
            "published": published,
            "epochs": dict(self.epochs),
            "stats": {"overlap_checks": self.overlap_checks},
        }


def process(events):
    """Replay an already validated event list in seq order."""
    ordered = sorted(events, key=lambda event: event["seq"])
    state = ReplayState()
    results = [state.apply(event) for event in ordered]
    return state.outcome(results)
