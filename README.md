# pitrkit

Point-in-time restore for a small key-value cluster. The cluster keeps fuzzy
base backups and an archive of log segments; `pitr.py restore` rebuilds the
database as it was at a chosen time or transaction boundary.

    python3 pitr.py restore <cluster> <out> (--to-time T | --to-tx X | --before-tx X)
    python3 pitr.py show <out>
    python3 -m unittest discover -s tests

`restore` prints `{"backup": name, "cut_lsn": L}` and exits 0, or prints
`{"error": ..., ...}` and exits 4. A log gap is reported as
`{"error": "log gap", "missing_from": L, "latest_recoverable_time": T}` where
`T` is the time of the last record before the gap.

## Cluster layout

`<cluster>/wal/<first>-<last>.jsonl` (8-digit LSNs) hold one record per line,
LSNs consecutive across segments and record times strictly increasing:

    {"lsn": 7, "time": 1070, "tx": 3, "type": "BEGIN"}
    {"lsn": 8, "time": 1080, "tx": 3, "type": "PUT", "table": "accounts", "key": "a1", "value": "90"}
    {"lsn": 9, "time": 1090, "tx": 3, "type": "DEL", "table": "accounts", "key": "a2"}
    {"lsn": 10, "time": 1100, "tx": 3, "type": "COMMIT"}

`ABORT` ends a transaction without effect. Writes take effect at COMMIT, in
commit order. A missing segment, a missing LSN or a line that does not parse is
a gap: nothing after it in the stream can be trusted for replay.

`<cluster>/backups/<name>/manifest.json` and `data.json`
(`{"tables": {"accounts": {"a1": "100"}}}`). The manifest holds `start_lsn`,
`start_time`, `redo_lsn`, and once the backup finished, `end_lsn` and
`end_time`. The data was copied while transactions kept committing: it holds
every commit up to `start_lsn` and an arbitrary part of the commits up to
`end_lsn`. `redo_lsn` is the first record a restore must read (the BEGIN of
the oldest transaction open at `start_lsn`).

## Restore rules

* A target time `T` includes exactly the transactions whose COMMIT time is
  `<= T`; a transaction still open at `T` is excluded. `--to-tx X` stops right
  after the COMMIT of `X`, `--before-tx X` right before it.
* The base backup is the newest finished backup whose `end_time <= T` (or
  `end_lsn` at or before the cut); unfinished backups are never used.
* Replay reads the log from `redo_lsn` and applies every transaction that
  committed after `start_lsn` and at or before the cut, in commit order.
* If the records needed for the target are not all in the archive, the restore
  fails with the log gap error; targets up to `latest_recoverable_time` still
  work.
* The restore is built in `<out>.restoring`, read back and checked, then
  replaces `<out>` as a whole. A failed restore leaves an existing `<out>`
  untouched.
