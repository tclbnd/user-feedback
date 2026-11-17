# occsim

A key/value engine with optimistic concurrency control (backward validation).

    python3 occsim.py <schedule.json>        # prints one JSON result per output step
    python3 -m unittest discover -s tests

## Transactions

A transaction reads the committed state as of its `begin` (plus its own
buffered writes) and buffers its writes until commit. It records a read set
(keys it read with `get`, and keys returned by `scan`), every `scan` range
`[lo, hi)`, and a write set (keys it `put` or `delete`d).

A `scan` remembers both the rows it returned and the range itself: it also
observed that no *other* key in the range exists, so a concurrent insert or
delete of a row it never saw invalidates it. `delete` removes the key from the
committed state (and from the deleting transaction's own view); deleted keys
are not stored as null.

Every committed update transaction gets the next commit number. At `begin` a
transaction remembers the last commit number. At `commit`:

- a transaction without writes commits immediately (read-only fast path,
  serialized at its `begin`);
- otherwise it is validated against every transaction that committed after its
  `begin`: if one of their written keys is in its read set or falls inside one
  of its scan ranges, it aborts; if not, validation and installing its writes
  happen as one atomic step and it gets the next commit number.

The result is equivalent to running the committed update transactions one
after another in commit order, with each read-only transaction at its `begin`.

## Schedule

A JSON list of steps; sessions are named by `s`:

    {"op": "begin", "s": "A"}
    {"op": "get", "s": "A", "key": "k"}              -> {"op","s","key","value"}   (null if absent)
    {"op": "scan", "s": "A", "lo": "a", "hi": "m"}   -> {"op","s","items": [[key, value], ...]}  (key order)
    {"op": "put", "s": "A", "key": "k", "value": "v"}
    {"op": "delete", "s": "A", "key": "k"}
    {"op": "commit", "s": "A"}                       -> {"op","s","result": "committed" | "aborted"}
    {"op": "dump"}                                   -> {"op","items": [[key, value], ...]}  (committed state)
