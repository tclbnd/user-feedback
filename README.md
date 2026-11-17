# shadowalter

Online schema change for a small JSON table store. Adding or dropping columns
rewrites the whole table; instead of locking it for the full copy, the tool
builds a shadow table while business writes keep arriving, then swaps the two
under a short lock.

    python3 osc.py migrate <db.json> <alter.json> [--workload W.json] [--chunk N]
                           [--max-lock T] [--fail-at copy|replay|cutover|swap]
    python3 osc.py show <db.json> [table]
    python3 -m unittest discover -s tests

`migrate` saves the database file in place and prints one JSON object:
`{"status": "done", "lock_ticks": N, "clock": N, "table": ..., "copied": N, "replayed": N}`
with exit status 0 (4 if the lock was held longer than `--max-lock`, default
2), or `{"status": "rolled_back", "reason": ..., "clock": N, ...}` with exit
status 3. `--fail-at` injects a failure: `copy` right after the first chunk is
copied, `replay` right after the first catch-up batch, `cutover` right after
the lock is taken, `swap` right before the tables are swapped.

## Files

Database: `{"clock": 0, "triggers": [], "tables": {"users": {"pk": "id",
"columns": ["id", "name", "email"], "defaults": {}, "rows": [{"id": 1, ...}]}}}`.
Primary keys are integers, rows are listed in key order. A trigger
`{"name", "table", "log"}` appends one row per business insert, update or
delete on `table` to the change log table `log`: `seq` (1, 2, ...), `op`
(`insert` / `update` / `delete`), `pk` (key after the change, or the deleted
key), `old_pk` (key before an update; differs when the key changed), `row`
(full row after the change, none for deletes).

ALTER: `{"table": "users", "add": [{"name": "tier", "default": "free"}], "drop": ["email"]}`.
Added columns take their default.

Workload: a JSON list of `{"tick": 3, "op": "insert", "table": "users", "row": {...}}`,
`{"tick": 3, "op": "update", "table": "users", "pk": 4, "set": {"name": "x", "id": 40}}`
(an update may change the key) and `{"tick": 5, "op": "delete", "table": "users", "pk": 2}`.
Inserting an existing key or touching a missing one does nothing. Each unit of
tool work (copying one chunk of `--chunk` rows, replaying one batch of at most
`--chunk` log entries) is followed by one tick of the logical clock, and a tick
runs the operations scheduled for it in file order. Operations on a locked
table wait and run in order at unlock, against the table carrying that name at
that moment. After success the remaining operations run before saving; after a
failure the file is saved as it is.

## Rules

* Shadow table `_osc_new_<t>`, change log `_osc_log_<t>`, trigger
  `_osc_trg_<t>` (installed before the copy). Rows are copied in key order,
  `--chunk` rows at a time.
* Changes are replayed in `seq` order; a row deleted and inserted again, or
  whose key changed, ends up exactly as in the source.
* Before the cut-over the tool replays outside the lock until at most one batch
  is left; the lock covers only that batch and the swap. `lock_ticks` is the
  longest stretch the table stayed locked.
* The swap renames `<t>` to `_osc_old_<t>` and the shadow to `<t>` in one
  catalog change, then drops the trigger, the change log and the old table.
* After a failure in any phase the original table, schema and rows are exactly
  what business traffic made of them, with no trigger, log or shadow left.
