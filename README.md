# CEA-608 caption display model

Run: `python3 cap.py input.json output.json`

Input `{"events":[{"t","data"}...]}` — events in time order, `t` is the
video timestamp in ms, `data` is a list of 2-byte pairs `[[lo,hi]...]`
(cc data). Byte semantics (subset):

- `0x14 0x25` / `0x1C 0x25`: RU2 / RU3 — roll-up, 2 or 3 rows.
- `0x14 0x2C` / `0x1C 0x2C`: EDM — erase displayed memory (clear screen).
- `0x14 0x2E` / `0x1C 0x2E`: ENM — erase non-displayed memory.
- `0x14 0x2F` / `0x1C 0x2F`: EOC — swap display/non-display (pop-on).
- `0x14 0x20` / `0x1C 0x20`: RCL — resume caption loading (pop-on mode).
- `0x14 0x2D` / `0x1C 0x2D`: CR — carriage return (roll-up: scroll).
- `0x14 0x21` / `0x1C 0x21`: BS — backspace on current row.
- `0x14 0x29` / `0x1C 0x29`: RDC — resume direct captioning (paint-on).
- Control words repeat-idempotent: an identical control pair received
  twice in a row counts ONCE (dedup applies only to adjacent repeats).
- Printable bytes 0x20-0x7F append to the current row in the active
  memory; `hi` == 0 means only `lo` carries a character.
- Modes: pop-on writes to NON-displayed memory (EOC publishes it);
  paint-on writes directly to the display; roll-up writes to the
  display and CR inserts a line break scrolling rows within the row
  window (RU2: 2 rows; RU3: 3 rows; oldest row drops off the top).
  EDM clears displayed memory; ENM clears non-displayed memory only.

Output `{"snapshots":[{"t","screen":[row0,row1,row2,row3]}...]}` — the
visible screen AFTER each event (4 rows, top index 0), strings right-
trimmed; a snapshot is emitted for every input event even when
unchanged. Also `"mode"` history: `{"modes":[{"t","mode"}...]}` when
the mode changes (initial mode `pop-on`; no entry at t0 unless a
control changes it).
