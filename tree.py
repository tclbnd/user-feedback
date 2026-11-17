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
        self.anchor = self.new(False)
        self.anchor.children = [self.new(True).id]

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

    def rebalance(self, parent, node, sibling):
        """Merge or redistribute an underfull leaf with an adjacent sibling."""
        i, j = parent.children.index(node.id), parent.children.index(sibling.id)
        if len(node.keys) + len(sibling.keys) <= MAX_KEYS:
            if j > i:
                node.keys += sibling.keys
                node.next = sibling.next
                sibling.keys = []
                del parent.keys[i], parent.children[j]
            else:
                sibling.keys += node.keys
                node.keys = []
                del parent.keys[j], parent.children[i]
            return
        if j > i:
            node.keys.append(sibling.keys.pop(0))
            parent.keys[i] = sibling.keys[0]
        else:
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
