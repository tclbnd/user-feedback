# rostr

A small snapshot-isolation key/value engine used by the on-call roster
service. Keys are integers, values are JSON scalars. Transactions run at
`serializable`: they read from a snapshot, and the engine cancels
transactions whose commits could not be produced by any serial order.

    python3 ssidb.py run <schedule.json>      # print the JSON outcome
    python3 ssidb.py report <schedule.json>   # one line per step, then who committed
    python3 -m unittest discover -s tests

## Schedule

    {"initial": {"1": "alice", "2": "bob"},
     "ops": [{"op": "begin", "tx": "T1"},
             {"op": "begin", "tx": "R", "read_only": true},
             {"op": "get", "tx": "T1", "key": 1},
             {"op": "scan", "tx": "T1", "lo": 1, "hi": 9},
             {"op": "put", "tx": "T1", "key": 2, "value": "carol"},
             {"op": "delete", "tx": "T1", "key": 2},
             {"op": "commit", "tx": "T1"}, {"op": "abort", "tx": "T1"}]}

Each transaction name is used once. `initial` is committed before the
schedule starts.

## Output

    {"results": [...one per op...], "committed": ["T2", "T1"],
     "aborted": ["T3"], "state": {"1": "alice"}}

`committed` is in commit order, `state` is the committed data at the end.
`get` returns `{"value": v}` (`null` if absent), `scan` returns
`{"rows": [[key, value], ...]}` for `lo <= key <= hi` in key order, everything
else `{"ok": true}`. Any operation may instead return

    {"error": "serialization_failure", "code": "40001", "retryable": true}

after which the transaction is rolled back and its later operations return
`{"error": "aborted"}`. A write to a key that a concurrent transaction has
already written (committed after our snapshot, or still pending) fails the
same way. Writes in a `read_only` transaction return `{"error": "read_only"}`.

## Snapshots

`begin` takes a snapshot of everything committed so far; reads see it plus
the transaction's own writes. Two transactions are concurrent when each began
before the other committed.

## Serializable rules

**Read registration.** Every `get` registers its key and every `scan`
registers its whole range `[lo, hi]`, including keys that do not exist. A
transaction's registrations stay in force after it commits, as long as
concurrent transactions can still write.

**rw-antidependency.** `R -> W` is recorded when `R` read a key (or a range
covering it) and a concurrent `W` writes, inserts or deletes that key, in
either order: when `R` reads and meets a version of `W` it cannot see, or when
`W` writes a key `R` has registered. Aborted transactions take no part.

**Dangerous structure.** `T1 -> T2 -> T3` (`T1` may be `T3`) is dangerous
when `T3` committed before both `T2` and `T1` committed. If `T1` was begun
`read_only`, it is dangerous only when `T3` committed before `T1` began;
otherwise the read-only transaction has a safe snapshot.

**Victim priority.** Structures are checked whenever an edge is recorded and
whenever a transaction commits. The engine cancels the pivot `T2` if it has
not committed, otherwise `T1`. A cancelled transaction fails with `40001` no
later than its next operation (its commit included). Workloads without
dangerous structures, such as disjoint data or conflicts that all point one
way, are never cancelled.
