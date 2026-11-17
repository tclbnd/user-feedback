# rangelock

A lock-based engine core for one table with one index, used to study
REPEATABLE READ under two-phase locking. All reads are locking reads
(`LOCK IN SHARE MODE` / `FOR UPDATE`), so repeatable reads and the absence of
phantoms must come from the locks alone. Locks are held until the transaction
commits or rolls back, and are all released together then.

    python3 lockdb.py run <script.json>     # JSON result
    python3 lockdb.py show <script.json>    # one line per step
    python3 lockdb.py show examples/acceptance.json
    python3 -m unittest discover -s tests

## Script

```json
{"unique": true, "rows": [10, 20, 30],
 "steps": [{"tx": 1, "op": "range", "low": 10, "high": 25},
           {"tx": 2, "op": "insert", "key": 25},
           {"tx": 1, "op": "get", "key": 20, "for_update": true},
           {"tx": 2, "op": "delete", "key": 10},
           {"tx": 1, "op": "commit"},
           {"tx": 2, "op": "rollback"}]}
```

`rows` are the index keys before the script starts; `unique` says whether the
index is unique. Steps run in order and a transaction starts with its first
step. `range` scans `low <= key <= high` (a bound may be `null`), `get` is an
equality lookup; both take shared locks, or exclusive ones with
`"for_update": true`. `delete` removes every row with the key, `insert` adds
one row. A step that has to wait is parked, and its transaction may not issue
another step until it resumes; every `commit`/`rollback` retries the parked
steps in the order they were parked.

Output: `{"results": [...], "table": [...]}`. Each result has `step`, `tx`,
`op`, `waited` (whether it blocked when issued), `status` (`ok`, `error` for a
duplicate key, or `waiting` if it never resumed), `rows` for reads (keys in
index order) or `count` for writes, and `resumed_at`, the index of the
commit/rollback step that let it finish. `table` is the live keys at the end.

## Locking rules

A *gap* is the open interval between an index entry and its predecessor; it is
named by the entry above it, or by the supremum after the last entry. A record
lock covers an entry, a gap lock the gap before it, a next-key lock both.

* A range scan takes a next-key lock on every entry inside the range
  (delete-marked entries too, though they are not returned) and a gap lock on
  the first entry past the end of the range.
* An equality hit on a **unique** index locks the record only. A miss locks
  the gap where the key would be. On a non-unique index an equality lookup
  takes next-key locks on the matching entries and a gap lock on the entry
  after them.
* `delete` locks like an equality lookup in exclusive mode and delete-marks
  the entries; they leave the index when the transaction commits.
* `insert` first requests an insert intention lock on the gap the key falls
  into. Insert intention conflicts only with gap and next-key locks of other
  transactions on that gap. The new entry is exclusively record-locked by its
  inserter. On a unique index, inserting a key that already has an entry
  first takes a shared record lock on that entry: if a running transaction has
  deleted it, the insert waits; if the key is still there afterwards the
  insert fails with a duplicate key error.
* Gap locks never conflict with each other, whatever their mode. Record parts
  follow the usual S/X matrix.
* When an entry is physically removed (committed delete, rolled back insert),
  its gap merges with the next gap, and gap locks on the removed entry go on
  protecting the merged gap.
