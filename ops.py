"""Insert, delete and range scan as latch-yielding coroutines.

Each operation yields ("S"|"X", node_id) to acquire a latch and ("U", node_id)
to release it; the scheduler resumes it once the request is granted."""
from tree import MAX_KEYS, MIN_KEYS


def descend(tree, key, mode):
    """Latch coupling from the anchor to the leaf for key.

    Returns the unlatched ancestors (top first) and the latched leaf."""
    node, path = tree.anchor, []
    yield mode, node.id
    while not node.leaf:
        child = tree.child_for(node, key)
        yield mode, child.id
        yield "U", node.id
        path.append(node)
        node = child
    return path, node


def insert(tree, key):
    path, leaf = yield from descend(tree, key, "X")
    if key not in leaf.keys:
        leaf.keys = sorted(leaf.keys + [key])
    node, held = leaf, [leaf]
    while len(node.keys) > MAX_KEYS:
        parent = path.pop()
        yield "X", parent.id
        held.append(parent)
        sep, right = tree.split(node)
        tree.add_child(parent, sep, right)
        node = parent
    for n in held:
        yield "U", n.id


def delete(tree, key):
    path, leaf = yield from descend(tree, key, "X")
    held = [leaf]
    if key in leaf.keys:
        leaf.keys.remove(key)
    if path and path[-1] is not tree.anchor and len(leaf.keys) < MIN_KEYS:
        parent = path[-1]
        yield "X", parent.id
        held.append(parent)
        i = parent.children.index(leaf.id)
        if len(parent.children) > 1:
            sibling = tree.nodes[parent.children[i - 1 if i > 0 else 1]]
            yield "X", sibling.id
            held.append(sibling)
            tree.rebalance(parent, leaf, sibling)
    for n in held:
        yield "U", n.id


def scan(tree, lo, hi):
    path, leaf = yield from descend(tree, lo, "S")
    out = []
    while True:
        out += [k for k in leaf.keys if lo <= k <= hi]
        nxt = leaf.next
        yield "U", leaf.id
        if nxt is None or (leaf.keys and leaf.keys[-1] >= hi):
            return out
        yield "S", nxt
        leaf = tree.nodes[nxt]
