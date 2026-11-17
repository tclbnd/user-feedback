# heapfile

A heap table stored in 256-byte slotted pages (`<dir>/heap.db`, page `i` at
offset `i * 256`). Rows are addressed by a row id `[page, slot]` that must
never change while the row lives.

    python3 heapctl.py <dir> <script.json>     # prints one JSON result per output step
    python3 -m unittest discover -s tests

Script steps: `{"op": "insert", "value": "..."}` -> `{"rid": [p, s]}`,
`{"op": "update", "rid": [p, s], "value": "..."}`, `{"op": "delete", "rid": [p, s]}`,
`{"op": "read", "rid": [p, s]}` -> `{"value": ...}`, `{"op": "stats"}` ->
`{"pages": [{"page", "kind", "slots", "free"}]}`. Values are UTF-8 strings.

## Data page

Bytes 0 `D`, 1 zero, 2–3 slot count, 4–5 `free_end` (records occupy
`[free_end, 256)`), then 4 bytes per slot: record offset and length
(uint16 big-endian each; length 0 = free slot). Free space of a page is
`256 - 6 - 4 * slots - sum(live record lengths)`; space freed by deletes or
shrinking updates is a hole until the page is compacted. Compaction packs the
records at the end of the page and keeps every slot number.

Records start with a tag byte:

| tag | body |
|-----|------|
| `T` | the value |
| `M` | the value of a row that moved here; only reachable through its forward pointer |
| `F` | 2-byte page + 2-byte slot of the `M` record holding the row |
| `L` | 2-byte first overflow page + 4-byte value length |

## Overflow page

Byte 0 `O`, 1–2 next overflow page (`0xFFFF` = last), 3–4 bytes used, then up
to 251 bytes of value. Values longer than 120 bytes are stored in a chain of
overflow pages and referenced by an `L` record.

## Placement rules

- Insert goes to the lowest data page with enough free space (counting 4
  bytes for a new slot unless a free slot exists), else a new page is
  appended. The lowest free slot is reused. If a page has enough free space
  but not enough contiguous space, it is compacted first.
- A page keeps a *forwarding reserve*: the free space never drops below
  `sum(max(0, 5 - len(rec)))` over its live records. A forwarding pointer is
  always 5 bytes, so this guarantees that any live slot can be turned into an
  `F` pointer no matter how the value grows or shrinks. Without it a page full
  of 1-byte rows could pin a row forever, because its slot could not even hold
  the pointer.
- Update writes in place when the new record fits the page (compacting if
  needed). Otherwise the row moves: the value goes to another page as an `M`
  record (an `L` record when it is longer than 120 bytes) and the original
  slot becomes an `F` pointer. A row that has already
  moved is moved again by rewriting the original `F` pointer, so a row is never
  more than one hop away from its row id; the old `M` record is removed.
- Delete frees the slot (and the `M` record of a moved row).
- Compaction packs live records at the end of the page and never renumbers or
  drops slots, so row ids survive it. It runs on a page when the space sitting
  in holes exceeds half the page, and also whenever an insert or grow would
  otherwise fail although the page has enough total free space.
