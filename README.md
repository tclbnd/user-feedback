# pagestore

A small page store with a write-ahead log (steal / no-force). Every change is
logged before the page changes; dirty pages are written back lazily. After a
crash, `store.py recover` must bring the data file back to the state of the
committed transactions.

    python3 store.py run <dir> <script.json>     # replay a workload, then stop
    python3 store.py recover <dir> [--crash-after-undo N]
    python3 store.py dump <dir>                  # print every page as JSON
    python3 -m unittest discover -s tests

`<dir>` holds `pages.db` and `wal.log`. `recover` prints
`{"checkpoint": L, "redo_start": L, "repaired_pages": [...], "undone": [tx, ...], "clrs": N}`,
where `checkpoint` is the begin LSN of the checkpoint analysis started from (0
if none). With `--crash-after-undo N` it exits with status 75 right after
logging its N-th CLR, without writing pages; running `recover` again must
finish the job.

Workload script: a JSON list of `{"op": "begin", "tx": 1}`,
`{"op": "set", "tx": 1, "page": 0, "slot": 2, "value": "abc"}`,
`{"op": "commit", "tx": 1}`, `{"op": "checkpoint"}`,
`{"op": "flush", "page": 0}` and `{"op": "flush", "page": 0, "torn": true}`
(power loss halfway through the page write). `{"op": "crash"}` or the end of
the script stops the process; unflushed pages are lost.

## Page format (`pages.db`)

256-byte pages, page `i` at offset `i * 256`. Bytes 0–1 `PG`, 2–5 page id
(uint32 BE), 6–13 page LSN (uint64 BE), 14–17 CRC-32 of the page computed with
this field zeroed, then 4 slots of 59 bytes (1 length byte + 58 UTF-8 bytes).
An all-zero page or a page past the end of the file is empty. A page whose CRC
does not match was torn and must not be trusted.

## Log format (`wal.log`)

One JSON object per line, LSNs strictly increasing from 1. A trailing line
without a newline is an interrupted write and must be ignored.

| type       | fields                                                    |
|------------|-----------------------------------------------------------|
| BEGIN, COMMIT, ABORT, END | `tx`, `prev`                               |
| UPDATE     | `tx`, `prev`, `page`, `slot`, `before`, `after`           |
| CLR        | `tx`, `prev`, `page`, `slot`, `after`, `undo_next`        |
| FPI        | `page`, `image` (hex of the page before its first update after a checkpoint) |
| CKPT_BEGIN | —                                                         |
| CKPT_END   | `begin`, `att` {tx: last LSN}, `dpt` {page: rec LSN}      |

`prev` chains one transaction's records backwards. A CLR restores the
`before` value of one UPDATE and its `undo_next` is that UPDATE's `prev`. A
checkpoint counts only once its CKPT_END is written.

## Recovery contract

Analysis starts from the last complete checkpoint; torn pages are restored
from their latest FPI; redo repeats history from the oldest recLSN; undo rolls
back every transaction without COMMIT in reverse LSN order, logging one CLR
per undone UPDATE and an END per finished rollback. Recovery must survive a
crash during its own undo pass.
