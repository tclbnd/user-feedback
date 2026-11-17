# mvstore

A multi-version key/value store with snapshot isolation. Every write creates a
new version; readers see the versions their snapshot allows; `vacuum` removes
versions no active snapshot can see.

    python3 mvstore.py <script.json>          # prints one JSON result per output step
    python3 -m unittest discover -s tests

## Transaction ids and commit log

Transaction ids (xids) are 32-bit: they start at 3 and after 4294967295 wrap
around to 3. Order between xids is circular: `a` precedes `b` when
`(a - b) mod 2^32` is at least `2^31`. The commit log records each xid as
`running`, `committed` or `aborted`.

A snapshot is taken when a transaction begins (repeatable read):
`xmax` = the next xid to be assigned, `xip` = xids running at that moment
(not the transaction's own), `xmin` = the oldest of `xip`, or `xmax` if `xip`
is empty. A xid counts as *in progress* for a snapshot when it does not
precede `xmax` or is in `xip`.

## Version chains

Each key has a chain of versions, newest first: `{"xmin", "xmax", "value"}`.
`xmin` is the creating xid, `xmax` the xid that replaced or deleted the
version (0 if none). A write marks the version it saw with its own xid as
`xmax` and pushes a new version; `value: null` deletes the key.

A version is visible to transaction `T` with snapshot `S` when its creator is
`T` itself (and `T` has not replaced it since), or its creator committed and is
not in progress for `S`; and its `xmax` is 0, aborted, still running, or in
progress for `S`. Reads return the first visible version's value, or `null`.

## Vacuum

The horizon is the oldest of all running xids and all running snapshots'
`xmin` (the next xid when nothing runs). Vacuum removes every version created
by an aborted transaction, and every version whose `xmax` committed and
precedes the horizon. The newest remaining version of each chain is always
kept.

## Script

A JSON list of steps; sessions are named by `s`:

    {"op": "begin", "s": "A"}                         -> {"op","s","xid"}
    {"op": "read", "s": "A", "key": "k"}              -> {"op","s","key","value"}
    {"op": "write", "s": "A", "key": "k", "value": "v"}
    {"op": "commit", "s": "A"}   {"op": "abort", "s": "A"}
    {"op": "vacuum"}                                  -> {"op","removed"}
    {"op": "chain", "key": "k"}                       -> {"op","key","versions": [[xmin, xmax, value], ...]}
    {"op": "next_xid", "value": 4294967294}           # only when nothing runs

Scripts never let two running transactions write the same key.
