# fkdb

An in-memory engine for tables linked by single-column foreign keys. It runs
DELETE and UPDATE statements and carries out the referential actions
`cascade`, `set_null`, `set_default`, `restrict` and `no_action`.

    python3 fkdb.py run <script.json>     # JSON: {"results": [...], "tables": {...}}
    python3 fkdb.py show <script.json>    # readable summary
    python3 -m unittest discover -s tests

## Script

```json
{"tables": {"dept": {"pk": "id"},
            "emp": {"pk": "id", "defaults": {"dept_id": 0}}},
 "rows": {"dept": [{"id": 0, "name": "unassigned"}, {"id": 1, "name": "R&D"}],
          "emp": [{"id": 10, "dept_id": 1}]},
 "foreign_keys": [{"name": "fk_emp_dept", "table": "emp", "column": "dept_id",
                   "ref_table": "dept", "ref_column": "id",
                   "on_delete": "set_default", "on_update": "cascade"}],
 "statements": [{"op": "update", "table": "dept", "where": {"id": 1}, "set": {"id": 2}},
                {"op": "delete", "table": "dept", "where": {"id": 2}}]}
```

Primary keys are integers and never collide. `where` is a column→value
equality map (empty: every row); target rows are processed in primary key
order. Both actions default to `no_action`. `defaults` gives the value used
by `set_default`.

## Execution order

Deleting or updating a row expands its referential actions depth-first: the
foreign keys that reference the row's table are applied **in definition
order**, and for each of them the referencing rows in primary key order. A
cascaded delete or update of a child is fully expanded before the next child.

* A row is removed before its own referencing rows are visited, and a row
  that is already gone is skipped. A row reached through several paths (a
  diamond) is therefore handled once, and a cycle of `cascade` deletes, or a
  self-referencing table, ends when it comes back to a deleted row.
* An update propagates through a foreign key only when the referenced column
  really changed. Cycles of `on_update` actions end there too.
* `restrict` fails immediately, when the first referencing row is found.
  `no_action` is checked when the whole statement has finished: it fails only
  if a referencing row still points at a value that no longer exists. A
  `set_default` whose default has no parent row fails the same way at the end
  of the statement.
* A failed statement leaves every table as it was before the statement.

## Output

One result per statement, `{"ok": true, "deleted": {table: n}, "updated":
{table: n}}` (distinct rows; a row deleted in the same statement is not
counted as updated), or `{"ok": false, "error": {"code":
"foreign_key_violation", "constraint": name, "row": [child table, child pk],
"at": [table, pk], "phase": "immediate" | "statement_end"}}`, where `at` is
the row whose delete or update hit the constraint. `tables` holds every table
sorted by primary key after the last statement.
