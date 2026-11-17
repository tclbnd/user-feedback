# upsertdb

An in-memory single-table engine used to replay bulk import scripts:
`INSERT ... ON CONFLICT`, UPDATE, DELETE and transactions, with unique
constraints checked either immediately (end of each statement) or at commit.

    python3 upsert.py run <script.json>     # JSON: {"results": [...], "table": [...]}
    python3 upsert.py show <script.json>    # readable summary
    python3 -m unittest discover -s tests

## Script

```json
{"table": {"columns": ["id", "email", "qty"],
           "constraints": [{"name": "pk", "columns": ["id"], "deferrable": true},
                           {"name": "uq_email", "columns": ["email"], "deferrable": true,
                            "initially": "deferred"}]},
 "rows": [{"id": 1, "email": "a@x", "qty": 1}],
 "statements": [
   {"op": "begin"},
   {"op": "insert", "rows": [{"id": 1, "email": "a@x", "qty": 5}],
    "on_conflict": {"target": "pk", "action": "update",
                    "set": {"qty": {"add": [{"col": "qty"}, {"excluded": "qty"}]}}}},
   {"op": "insert", "rows": [{"id": 3}], "on_conflict": {"action": "nothing"}},
   {"op": "update", "where": {"id": {"in": [1, 3]}}, "set": {"id": {"sub": [4, {"col": "id"}]}}},
   {"op": "set_constraints", "names": ["uq_email"], "mode": "immediate"},
   {"op": "delete", "where": {"id": 3}},
   {"op": "select"},
   {"op": "commit"}]}
```

Missing columns are NULL. Constraints are immediate unless `"initially":
"deferred"`; only `"deferrable": true` constraints can be deferred, initially
or with `set_constraints` (`names` is a list or `"all"`). Modes return to their
initial value when the transaction ends; switching to immediate checks the
constraint at once. Expressions: a literal, `{"col": c}` (current row),
`{"excluded": c}` (row proposed for insertion), `{"add": [a, b]}`,
`{"sub": [a, b]}`; NULL in arithmetic gives NULL. `where` maps a column to a
value (NULL never matches) or to `{"in": [...]}`. A statement outside
`begin`/`commit` runs in its own transaction; a transaction still open at the
end of the script is rolled back.

## Unique constraint rules

* Keys containing NULL never collide.
* **Immediate** constraints must hold at the end of every statement; rows may
  collide while one UPDATE is still running (swapping two primary keys in one
  UPDATE is fine). **Deferred** constraints are checked only at commit; if one
  is violated then, the commit fails and the whole transaction is rolled back.
* INSERT handles its rows in order. For each row the immediate constraints
  are examined **in definition order** against the table as it is at that
  moment (including rows written earlier by the same statement); the first
  constraint with a colliding row decides:
  * no `on_conflict`, or a `target` naming another constraint: the statement
    fails with `unique_violation`, `row` = this row's index in the statement;
  * `"action": "nothing"`: the row is skipped (no `target` means any constraint);
  * `"action": "update"`: the colliding row is updated with `set`. A `target`
    is required and must be immediate at that time (`invalid_target`
    otherwise). If the colliding row was already inserted or updated by the
    same statement, the statement fails with `cardinality_violation` at this row.
  Deferred constraints take no part in conflict handling.
* After the last row the immediate constraints are checked once more over the
  whole table; errors found there or at commit have `row` = null and report
  the smallest colliding key of the first violated constraint.
* A failed statement is undone completely but does not abort the
  transaction: earlier statements keep their effects.

## Output

One result per statement: `{"ok": true, "inserted", "updated", "skipped"}`
(INSERT), `{"ok": true, "updated"}`, `{"ok": true, "deleted"}`,
`{"ok": true, "rows"}` (SELECT), `{"ok": true}`, or `{"ok": false, "error":
{"code", "constraint", "key", "row", "at"}}` with `at` = `statement` or
`commit`. `table` lists the final rows in insertion order.
