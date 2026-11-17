"""B+ tree node layout and structural changes. Callers hold the latches."""
from bisect import bisect_right

MAX_KEYS, MIN_KEYS = 4, 2


class Node:
    def __init__(self, nid, leaf, keys=None, children=None):
        self.id, self.leaf = nid, leaf
        self.keys, self.children = keys or [], children or []
        self.next = None


class Tree:
    def __init__(self):
        self.nodes = {}
        # Bumped once per committed insert/delete.
        self.version = 0
        # Immutable committed key set for every version, so a scan can read a
        # single instant without racing key-set changes.
        self.snapshots = {0: frozenset()}
        self.anchor = self.new(False)
        self.anchor.children = [self.new(True).id]

    def commit(self):
        """Advance the version and record the newly committed key set."""
        self.version += 1
        self.snapshots[self.version] = frozenset(self.key_set())

    def snapshot(self, version=None):
        """Return the immutable key set visible at ``version`` (default: the
        latest committed instant)."""
        if version is None:
            version = self.version
        return self.snapshots[version]

    def new(self, leaf, keys=None, children=None):
        node = Node(len(self.nodes), leaf, keys, children)
        self.nodes[node.id] = node
        return node

    def child_for(self, node, key):
        return self.nodes[node.children[bisect_right(node.keys, key)]]

    def split(self, node):
        if node.leaf:
            mid = (len(node.keys) + 1) // 2
            right = self.new(True, node.keys[mid:])
            right.next, node.next = node.next, right.id
            node.keys = node.keys[:mid]
            return right.keys[0], right
        mid = len(node.keys) // 2
        sep = node.keys[mid]
        right = self.new(False, node.keys[mid + 1:], node.children[mid + 1:])
        node.keys, node.children = node.keys[:mid], node.children[:mid + 1]
        return sep, right

    def add_child(self, parent, sep, right):
        if parent is self.anchor:
            old = self.nodes[parent.children[0]]
            parent.children[0] = self.new(False, [sep], [old.id, right.id]).id
            return
        i = bisect_right(parent.keys, sep)
        parent.keys.insert(i, sep)
        parent.children.insert(i + 1, right.id)

    def rebalance(self, parent, node, sibling, i, j):
        """Merge or redistribute an underfull leaf with an adjacent sibling.

        ``i`` and ``j`` are the current, caller-verified positions of ``node``
        and ``sibling`` in ``parent.children`` (|i - j| == 1).  The caller
        holds X latches on all three nodes."""
        assert abs(i - j) == 1
        if len(node.keys) + len(sibling.keys) <= MAX_KEYS:
            if j > i:
                node.keys += sibling.keys
                node.next = sibling.next
                sibling.keys = []
                del parent.keys[i], parent.children[j]
            else:
                sibling.keys += node.keys
                # The left sibling takes the merged node's place in the
                # leaf chain; otherwise a scan following next would land on
                # the emptied node instead of the rest of the chain.
                sibling.next = node.next
                node.keys, node.next = [], None
                del parent.keys[j], parent.children[i]
            return
        # Redistribution: the two leaves together hold more than MAX keys,
        # so move enough keys for both to reach the minimum instead of a
        # single key that could leave the underfull node below MIN_KEYS.
        total = len(node.keys) + len(sibling.keys)
        target = max(MIN_KEYS, total // 2)
        if j > i:
            # sibling is on the right: shift its smallest keys left.
            while len(node.keys) < target:
                node.keys.append(sibling.keys.pop(0))
            parent.keys[i] = sibling.keys[0]
        else:
            # sibling is on the left: shift its largest keys right.
            while len(node.keys) < target:
                node.keys.insert(0, sibling.keys.pop())
            parent.keys[j] = node.keys[0]

    def dump(self):
        root = self.anchor.children[0]
        out, todo = {}, [root]
        while todo:
            node = self.nodes[todo.pop(0)]
            out[str(node.id)] = {"leaf": node.leaf, "keys": node.keys, "children": node.children,
                                 "next": node.next}
            todo += node.children
        return {"root": root, "nodes": out}

    def key_set(self):
        """Committed keys reachable from the root, following the leaf chain."""
        node = self.nodes[self.anchor.children[0]]
        while not node.leaf:
            node = self.nodes[node.children[0]]
        keys, seen = [], set()
        while node is not None:
            for k in node.keys:
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
            node = self.nodes[node.next] if node.next is not None else None
        return keys
