# rowexec

Row-level DML execution: column defaults, stored generated columns and
BEFORE / AFTER row triggers, used by the billing tables.

## Layout

- `rowexec/expr.py` – expressions for defaults, generated columns and trigger actions
- `rowexec/schema.py` – table definitions and trigger lookup
- `rowexec/dml.py` – INSERT, UPDATE and INSERT ... ON CONFLICT DO UPDATE execution
- `runsql.py` – runs a list of statements against one table definition

## Running

    python3 runsql.py examples/invoice.json out.json
    python3 -m unittest discover -s tests

## Execution order (what the billing team relies on)

For every row of an INSERT: column defaults fill the columns the statement
did not give (each row gets its own default value, e.g. its own `nextval`);
BEFORE triggers run in trigger-name order and may change NEW; stored
generated columns are computed from the final NEW; NOT NULL and key checks
run; the row is stored. AFTER row triggers run once the whole statement has
processed all its rows, row by row in statement order and, for each row, in
trigger-name order; they see the stored row including generated columns.

UPDATE works the same way minus defaults: SET expressions are evaluated
against the old row, then BEFORE triggers, generated columns, checks, store,
and AFTER triggers at the end of the statement.

INSERT ... ON CONFLICT (key) DO UPDATE (the nightly price import uses it):
every proposed row first goes through defaults and BEFORE INSERT triggers
exactly as for an INSERT, so a `nextval` default is used up even when the
row then conflicts. If the key does not exist yet, the row continues as an
INSERT. If it exists, the conflict SET expressions are evaluated against
the existing row, where `{"excluded": col}` is the proposed row (after its
BEFORE INSERT triggers); then BEFORE UPDATE triggers, generated columns,
checks and store follow as for an UPDATE. At the end of the statement the
AFTER triggers fire row by row in statement order: AFTER INSERT for rows
that were inserted, AFTER UPDATE for rows that were updated. A statement
that would insert or update the same key twice fails.

A BEFORE trigger assigning a generated column is an error. As everywhere
else in the database, a statement that fails has no effect.
