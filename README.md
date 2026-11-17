# lrukpool

A buffer pool for a small page store: LRU-K replacement, pin counts,
write-ahead logging and checkpoints. Traces of page accesses drive it from the
command line.

    python3 bp.py run <dir> <trace.json> [--frames N] [--k K] [--crash-at-write N]
    python3 bp.py recover <dir>
    python3 bp.py dump <dir>
    python3 -m unittest discover -s tests

`run` prints `{"hits": N, "misses": N, "evictions": [page, ...], "checkpoints":
[redo_lsn, ...], "errors": [{"step": i, "error": "BUFFER_FULL"}]}`. The end of
the trace is a crash: frames still in memory and log records not yet flushed
are lost. `--crash-at-write N` crashes right before the N-th data page write
(exit status 75). `recover` replays the durable log into `data.db`.

## Trace

A JSON list of `{"op": "get", "page": p}` (fetch and release),
`{"op": "scan", "from": a, "to": b}` (get pages a .. b-1),
`{"op": "pin", "page": p}`, `{"op": "unpin", "page": p}`,
`{"op": "write", "page": p, "slot": s, "value": "v"}` (fetch, log, change, release),
`{"op": "commit"}` (flush the log), `{"op": "flush", "max": n}` (background
writer: write back up to n unpinned dirty frames, oldest first modification
first) and `{"op": "checkpoint"}`.

## Files

`data.db`: 64-byte pages, page `p` at offset `p * 64`: page LSN (uint64 BE),
then 4 slots of 1 length byte + 13 UTF-8 bytes. Missing pages are empty.
`wal.log`: one JSON record per line, `{"lsn", "page", "slot", "value"}` or
`{"lsn", "type": "checkpoint", "redo_lsn"}`; only lines in the file are durable.

## Rules

* Every fetch is one access and gets the next timestamp. A frame remembers the
  timestamps of its accesses since it was loaded.
* LRU-K: the victim is the unpinned frame with the largest backward K-distance
  (now minus the timestamp of its K-th most recent access). Frames with fewer
  than K accesses have infinite distance; among them the one whose first access
  is oldest goes first. A scan therefore does not push out pages touched K
  times.
* `pin` adds one to the pin count, `unpin` removes one. A frame with a pin
  count above zero is never evicted. When a page must be loaded and every
  frame is pinned, the operation fails with `BUFFER_FULL` and the trace goes
  on; it does not wait.
* Before a dirty page is written to `data.db`, the log must be durable up to
  and including the record of its latest change (page LSN).
* A checkpoint logs `redo_lsn`: the smallest LSN that first dirtied a frame
  still dirty (the next LSN if none), then flushes the log. Recovery redoes
  every record from the last checkpoint's `redo_lsn` whose LSN is above the
  page LSN on disk.
