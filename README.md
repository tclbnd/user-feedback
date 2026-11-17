# logrep

The subscriber side of a logical replication setup. The publisher keeps a
change log and a replication slot; the subscriber streams changes from it and
applies them to a local replica. The connection can drop at any message and
the subscriber process can be killed at any time; running `sync` again must
converge to exactly the publisher's data, never applying a transaction twice
and never skipping one.

    python3 subscriber.py sync <replica_dir> <publisher_dir> [--drop-after N] [--kill-after-commits K]
    python3 -m unittest discover -s tests

`sync` streams until the end of the change log and prints
`{"position": L, "applied": N}` (N = transactions applied in this run).
Fault injection for tests: `--drop-after N` makes the publisher drop the
connection after delivering N messages (exit status 75);
`--kill-after-commits K` kills the process (exit status 137) right after its
K-th local transaction commit has replaced `replica.json`.

## Publisher (`publisher.py`, given)

`<publisher_dir>/changes.jsonl` is the change log; `slot.json`
(`{"confirmed_lsn": L}`) is the slot. A connection started at LSN `S` streams
every message with `lsn > max(S, confirmed_lsn)`. The subscriber reports
progress with `feedback(lsn)`, which advances `confirmed_lsn`; the publisher
treats everything up to it as received for good.

Change log, one JSON object per line, LSNs increasing. Transactions are not
interleaved; each is `begin`, its changes, `commit`:

    {"lsn": 1, "type": "begin", "xid": 7}
    {"lsn": 2, "type": "ddl", "xid": 7, "op": "create_table", "table": "acct", "primary_key": "id", "columns": {"id": null, "owner": null}}
    {"lsn": 3, "type": "insert", "xid": 7, "table": "acct", "row": {"id": 1, "owner": "ann"}}
    {"lsn": 4, "type": "update", "xid": 7, "table": "acct", "key": 1, "row": {"id": 1, "owner": "bo"}}
    {"lsn": 5, "type": "delete", "xid": 7, "table": "acct", "key": 1}
    {"lsn": 6, "type": "ddl", "xid": 7, "op": "add_column", "table": "acct", "column": "tier", "default": "basic"}
    {"lsn": 7, "type": "ddl", "xid": 7, "op": "drop_column", "table": "acct", "column": "tier"}
    {"lsn": 8, "type": "commit", "xid": 7}

`key` is the primary-key value of the row before the change. `row` carries
the columns that exist at that point of the log; a missing column is null.
`add_column` gives existing rows the default; `columns` maps names to
defaults.

## Replica (`<replica_dir>`)

- `replica.json`: `{"tables": {"acct": {"primary_key": "id", "columns": {"id": null, ...}, "rows": [{...}, ...]}}}`,
  rows sorted by primary key. A replica may start with rows of its own.
- `position.json`: `{"commit_lsn": L}`, the commit LSN of the last applied
  transaction (0 or missing before the first one).
- `conflicts.jsonl`: one line per conflict,
  `{"commit_lsn": C, "lsn": l, "type": T, "table": t, "key": k}`.
- `journal.json`: internal, the durable commit record. It holds the tables,
  `commit_lsn` and the conflict list together. See below.

## Apply rules

Changes are applied in log order, one transaction at a time and atomically:
a transaction is either fully visible in `replica.json` or not at all, and
the position moves with it. DDL is applied at its place among the data
changes of its transaction. Conflict policy:

| type            | situation                     | action                        |
|-----------------|-------------------------------|-------------------------------|
| `insert_exists` | insert of an existing key     | overwrite the row, log it     |
| `update_missing`| update of a missing key       | skip the change, log it       |
| `delete_missing`| delete of a missing key       | skip the change, log it       |

Every conflict is logged exactly once, however often the connection drops.

## Crash safety

Reconnects are safe because the commit position is only ever a transaction
boundary and because all recoverable state commits in one atomic step:

1. The transaction is applied to an in-memory copy of the replica. Nothing on
   disk changes, so a drop mid-transaction leaves the replica untouched.
2. At `commit`, `journal.json` (tables + `commit_lsn` + conflict list) is
   written to a temp file, `fsync`ed and `os.replace`d over the old one. That
   rename is the commit point: data, position and conflict log become visible
   together or not at all.
3. `replica.json`, `conflicts.jsonl` and `position.json` are re-rendered from
   the journal afterwards, with `position.json` last, so it never names a
   transaction the data or the conflict log do not contain.
4. On startup the journal is the source of truth and the derived files are
   healed before streaming, so dying during step 3 is harmless.

Consequences:

- A transaction is resumed exactly at its start: the stored position is a
  commit LSN. If a position is not a commit boundary (left behind by an older
  subscriber), it is rewound to the previous commit boundary and that
  transaction is replayed in full.
- A transaction delivered again after a reconnect (`commit_lsn` already
  applied) is skipped as a whole, so it is never applied twice and its
  conflicts are never logged twice.
- Progress is fed back to the publisher only after the local commit, so the
  slot can never run ahead of what the replica durably has. If it already has
  (log truncated), `sync` says so on stderr rather than applying a suffix.

