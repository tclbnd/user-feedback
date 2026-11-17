# idxcheck

Consistency check and online repair of secondary indexes for a small heap
table. After a crash an index can point at deleted rows, miss new rows or carry
stale keys, so a query answers differently depending on the access path.

    python3 idxtool.py check <dir>                      # print the report
    python3 idxtool.py repair <dir> [--writes W.json] [--crash-after N]
    python3 idxtool.py query <dir> <index> <key>        # rows via the index and via a full scan
    python3 -m unittest discover -s tests

## Files in `<dir>`

`catalog.json`: `{"columns": ["id", "email", "city"], "indexes": [{"name": "email",
"column": "email", "unique": true}, {"name": "city", "column": "city", "unique": false}]}`.

`heap.db`: 512-byte pages. Bytes 0–1 `HP`, 2–3 page number (uint16 BE), 4–7
zero, then 8 slots of 63 bytes: flag (0 free, 1 live, 2 deleted), length, and
up to 61 bytes of the row as compact JSON (`[7,"g@x","Oslo"]`). A deleted slot
keeps its old bytes until it is reused. A row id is `[page, slot]`. Inserts
take the first slot that is not live.

`idx_<name>.db`: bytes 0–1 `IX`, 2–5 entry count (uint32 BE), 6–15 zero, then
40-byte entries sorted by (key, page, slot): key length, key (33 bytes UTF-8,
zero padded), page (uint32 BE), slot (uint16 BE).

## Report

`check` prints `{"problems": [...]}`, each problem `{"index", "kind", "key",
"rowid"}`, sorted by (index, kind, key, rowid):

* `dangling`: an entry whose row id is not a live row (free, deleted or out of
  range); `key` is the entry's key;
* `mismatch`: an entry pointing at a live row whose column holds another value;
* `missing`: a live row without an entry (its value, its row id) — reported
  per row, even when other rows share the key;
* `duplicate`: in a unique index, every live row after the first (lowest row
  id) that holds the same value.

## Repair

`repair` turns the report into actions and writes them to `<dir>/repair.log`
(one JSON line each, `{"seq", "op", "index", "key", "rowid"}`) before touching
anything: duplicates first (`quarantine`: the row is appended to
`quarantine.jsonl` as `{"rowid", "row"}` and deleted through the normal write
path), then `drop` for dangling and mismatched entries, then `add` for missing
ones. After each action the files are saved and `{"done": seq, "applied": bool}`
is logged. Ordinary writes keep running during the repair: `--writes` is a JSON
list of `{"before_action": n, "op": "insert", "row": [...]}`,
`{"op": "delete", "rowid": [p, s]}` or `{"op": "update", "rowid": [p, s], "row": [...]}`,
applied through the normal write path (which maintains every index) right
before action `n`, the rest at the end. Because of them an action is re-checked
against the current files just before it runs and skipped when it is no longer
needed. `--crash-after N` stops with status 75 after the N-th action is saved
but before it is logged as done; running `repair` again resumes the logged plan.
The output is `{"plan": N, "applied": N, "skipped": N, "remaining": [...]}`,
where `remaining` is a fresh check that must be empty.
