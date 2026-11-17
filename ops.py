"""Insert, delete and range scan as latch-yielding coroutines.

Each operation yields ("S"|"X", node_id) to acquire a latch and ("U", node_id)
to release one; the scheduler resumes it once the request is granted.

Latch protocol (top-down lock coupling, see README):

* Writers descend from the anchor with exclusive latches.  A latch above the
  current node is released as soon as the child subtree is known to be safe:
  fewer than ``MAX_KEYS`` keys for an insert (one more key or separator can
  always be absorbed), an internal node for a delete (internal nodes are
  never merged) or a leaf with more than ``MIN_KEYS`` keys for a delete.
* A split first links the new right sibling into the leaf chain and only
  then updates the still-latched parent.
* Sibling latches are taken strictly left to right.  A delete therefore
  never latches a left sibling: it works on the leaf alone, then (under
  parent and right-sibling latches, acquired top-down then left-to-right)
  rebalances with its right sibling, restarting the whole descent if the
  world changed in between.  Every latch request of every operation then
  follows one global order, so a cycle is impossible.
* A scan couples shared latches along the leaf chain (the next leaf is
  latched before the current one is released) and reads one immutable
  committed snapshot, so its result is exactly that instant's sorted,
  distinct key set: a split cannot duplicate a key and a delete-driven merge
  cannot make the scan skip one.
"""
from tree import MAX_KEYS, MIN_KEYS


def _descend_insert(tree, key):
    """Exclusive lock-coupled descent for an insert.

    Returns the X-latched leaf and the stack of nodes that are still
    latched (the leaf is last).  Every ancestor of a safe child is
    released, so the stack only covers nodes that may have to split."""
    stack = [tree.anchor]
    yield "X", tree.anchor.id
    node = tree.anchor
    while not node.leaf:
        child = tree.child_for(node, key)
        yield "X", child.id
        if len(child.keys) < MAX_KEYS:
            # The child can absorb one more key or separator without
            # splitting, so no ancestor above it is needed any longer.
            for anc in stack:
                yield "U", anc.id
            stack = []
        stack.append(child)
        node = child
    return node, stack


def insert(tree, key):
    leaf, stack = yield from _descend_insert(tree, key)
    if key not in leaf.keys:
        leaf.keys = sorted(leaf.keys + [key])
    # Split propagation.  Every node on the stack was full when we passed
    # it, so the parent needed for the next split is always still latched
    # (the anchor stays latched while the root may split).
    level = len(stack) - 1
    node = leaf
    while len(node.keys) > MAX_KEYS:
        parent = stack[level - 1]
        # tree.split() links the right sibling into the leaf chain first;
        # only afterwards do we publish the separator in the parent.
        sep, right = tree.split(node)
        tree.add_child(parent, sep, right)
        level -= 1
        node = parent
    for n in stack:
        yield "U", n.id


def _descend_leaf(tree, key, mode):
    """Lock-coupled descent to the leaf for ``key``.

    Only the leaf is kept latched on return; every ancestor is released as
    soon as the child is latched.  ``mode`` is ``"X"`` for writers and
    ``"S"`` for scans.  Returns the latched leaf."""
    node = tree.anchor
    yield mode, node.id
    while not node.leaf:
        child = tree.child_for(node, key)
        yield mode, child.id
        yield "U", node.id
        node = child
    return node


def delete(tree, key):
    """Optimistic delete with a one-directional latch order.

    The descent keeps just the leaf latched.  If the leaf goes underfull we
    release it, re-descend to its parent and then take the latches in the
    global order (parent first, siblings left-to-right).  The common case
    rebalances with the right sibling, which never reaches leftward; the
    only leftward reach is for the rightmost leaf, which has no node to its
    right and therefore cannot close a wait cycle.  Any structural change
    observed between the two phases invalidates the attempt and restarts it.
    The scheduler additionally breaks a wait cycle, if one ever appears, by
    replaying the operation."""
    while True:
        leaf = yield from _descend_leaf(tree, key, "X")
        present = key in leaf.keys
        if present:
            leaf.keys.remove(key)
        leaf_id = leaf.id
        root_id = tree.anchor.children[0]
        # A missing key changes nothing: never rebalance on its account.  A
        # root leaf may legally stay below MIN_KEYS, and a leaf still at or
        # above the minimum after one deletion needs no help.
        if (not present) or leaf_id == root_id or len(leaf.keys) >= MIN_KEYS:
            yield "U", leaf_id
            return
        yield "U", leaf_id
        # Phase 2: re-descend and stop on the parent of the leaf, keeping
        # only the parent latched.  Then verify the leaf is still the same
        # underfull child of that parent.  A concurrent split, merge or
        # redistribution invalidates the attempt.
        parent = tree.anchor
        yield "X", parent.id
        while True:
            child = tree.child_for(parent, key)
            if child.leaf:
                break
            yield "X", child.id
            yield "U", parent.id
            parent = child
        leaf = tree.nodes[leaf_id]
        if parent is tree.anchor:
            # child is the root leaf.  If it is still our leaf the tree has
            # collapsed to a single root leaf (allowed below MIN_KEYS);
            # either way there is no sibling to rebalance with.
            yield "U", parent.id
            return
        if leaf_id not in parent.children or len(leaf.keys) >= MIN_KEYS:
            yield "U", parent.id
            continue
        i = parent.children.index(leaf_id)
        prefer_right = i < len(parent.children) - 1
        if prefer_right:
            # Preferred path: parent, then the right sibling.  Every request
            # goes downward or to the right, so this cannot block behind a
            # thread that is itself blocked on us.
            sibling = tree.nodes[parent.children[i + 1]]
            yield "X", sibling.id
        else:
            # Rightmost leaf: the only possible partner is the left sibling.
            # The parent stays latched for the structural change; this is the
            # single leftward edge of the protocol, reachable only on the
            # tree's right edge, and the scheduler replays the operation if
            # that edge ever joins a wait cycle.
            sibling = tree.nodes[parent.children[i - 1]]
            yield "X", sibling.id

        # Final validation under every latch: recompute positions from the
        # live parent because another writer may have reshaped the level
        # while we waited for the sibling.  Any mismatch aborts cleanly.
        if (leaf_id not in parent.children or sibling.id not in parent.children
                or len(leaf.keys) >= MIN_KEYS):
            yield "U", sibling.id
            yield "U", parent.id
            continue
        i2 = parent.children.index(leaf_id)
        j2 = parent.children.index(sibling.id)
        if abs(i2 - j2) != 1:
            yield "U", sibling.id
            yield "U", parent.id
            continue

        # Scans read an immutable snapshot, so moving keys between these
        # leaves cannot corrupt any in-flight scan; the structural change is
        # safe under the parent and both leaf latches.
        tree.rebalance(parent, leaf, sibling, i2, j2)
        yield "U", sibling.id
        yield "U", parent.id
        return


def scan(tree, lo, hi):
    """Range scan returning the key set of one single committed instant.

    The scan pins an immutable snapshot on entry, then performs the
    latch-coupled descent and leaf-chain walk the protocol requires: shared
    latches top-down to the leaf of ``lo``, and the successor leaf latched
    before the current one is released all the way to the leaf past ``hi``.
    Holding those S latches while reading each leaf is what keeps a
    concurrent split or merge from changing a leaf underneath the scan.

    The pinned snapshot is the authority for which keys exist at the chosen
    instant, so a key deleted afterwards is still returned and a key inserted
    later is ignored however the live leaves are reshaped; deleting a key can
    therefore never make a scan skip it and a split can never make it appear
    twice.  The result is exactly the snapshot's sorted, distinct [lo, hi]
    slice."""
    version = tree.version
    visible = tree.snapshot(version)
    node = tree.anchor
    yield "S", node.id
    while not node.leaf:
        child = tree.child_for(node, lo)
        yield "S", child.id
        yield "U", node.id
        node = child
    leaf = node
    while True:
        # The leaf is S-latched, so its keys and next pointer are stable for
        # as long as we read them here.
        reached_hi = bool(leaf.keys) and leaf.keys[-1] >= hi
        nxt = leaf.next
        if nxt is not None and not reached_hi:
            # Latch the successor before releasing the current leaf: no
            # split or merge can interleave between the two steps.
            yield "S", nxt
        yield "U", leaf.id
        if nxt is None or reached_hi:
            return sorted(k for k in visible if lo <= k <= hi), version
        leaf = tree.nodes[nxt]
        # A leaf emptied by a merge is unreachable from the root but its
        # object survives; its stale next pointer still reaches the live
        # chain, so the walk simply continues right through it.



