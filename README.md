# linktree

A B+ tree index with node latches, exercised by a deterministic scheduler so
that concurrent inserts, deletes and range scans can be replayed exactly.

    python3 bptree.py run <workload.json> [--seed N]    # print the JSON outcome
    python3 bptree.py show <workload.json> [--seed N]   # scan results and the tree, level by level
    python3 -m unittest discover -s tests

## Workload

    {"initial": [2, 4, 6],
     "threads": [[{"op": "insert", "key": 5}, {"op": "delete", "key": 2}],
                 [{"op": "scan", "lo": 1, "hi": 9}]]}

`initial` keys are inserted one by one before the threads start. Each thread
runs its operations in order. Inserting a present key or deleting a missing
key does nothing.

Output: `{"deadlock": false, "steps": n, "results": [[...], ...], "tree": {...}}`.
`results[t]` holds one entry per operation of thread `t`: `{"ok": true}` for
insert and delete, `{"keys": [...]}` for a scan. When every unfinished thread
waits for a latch the run stops with `"deadlock": true` and `"waiting"`
listing each blocked request. `tree` is
`{"root": id, "nodes": {id: {"leaf", "keys", "children", "next"}}}` with
every node reachable from the root.

## Scheduler

Operations are coroutines that yield `("S", node)` or `("X", node)` to acquire
a shared or exclusive latch and `("U", node)` to release one. At each step the
scheduler picks, with `random.Random(seed)`, one thread whose pending request
can be granted. Node contents are only read or changed between yields, while
the right latches are held.

## Node layout

Node 0 is an anchor whose single child is the root; it is latched first by
every operation, so a root split or a new root is covered by the anchor latch.
Every node holds at most 4 keys. An internal node with keys `k1..kn` has
`n + 1` children, and child `i` holds keys `k_i <= key < k_(i+1)`. Leaves are
linked left to right through `next`.

- A leaf with 5 keys splits: the left keeps the first 3, a new right sibling
  takes the rest, and its first key goes up as the separator. An internal node
  with 5 keys keeps 2, moves the middle key up and gives the last 2 (with
  their children) to the new right sibling.
- A non-root leaf below 2 keys rebalances with its left sibling under the same
  parent (its right sibling if it is the first child): if both fit in one
  node the right one is merged into the left, otherwise one key moves over
  and the separator is updated. Internal nodes are never merged.

## Latch protocol

- Writers descend from the anchor with exclusive latches (lock coupling). As
  soon as a child is known not to split (fewer than 4 keys, for insert) or
  not to need its parent (an internal node, or a leaf above 2 keys, for
  delete), every latch above it is released.
- A split links the new right sibling into the leaf chain before the
  separator is added to the parent, which is still latched.
- Latches are taken top-down, and between siblings strictly left to right: a
  leaf that rebalances with its left sibling releases itself, latches the left
  sibling, then latches itself again.
- A scan descends with shared latches to the leaf of `lo`, then walks the
  leaf chain: it latches the next leaf before releasing the current one and
  never returns a key that is not greater than the last key it returned.
- No sequence of operations may deadlock, every scan returns sorted distinct
  keys, and a scan returns every key that was present for its whole duration.
