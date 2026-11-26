# antickit

Check for Atari 400/800/XL/XE screens captured by the art team's emulator
as memory snapshots (`.xlm`). `dlinfo.py` follows the ANTIC display list of
each snapshot and prints one JSON summary, so a converted title screen can be
checked against what the machine would actually display.

## File layout

`XLMEM`, a zero byte, the display list address (DLISTL/DLISTH, 16-bit
little-endian), the DMACTL value, then the full 64 KB of RAM. ANTIC reads
the display list and screen memory from that RAM exactly as the hardware
does; the frame ends at the display list's JVB instruction.

## Output

| field | meaning |
|---|---|
| `scanlines` | scan lines ANTIC generates before the JVB (blank and mode lines) |
| `mode_lines` | number of mode line instructions |
| `blank_lines` | scan lines that show only background |
| `dli` | instructions with the display list interrupt bit set |
| `lms` | instructions that load the memory scan counter |
| `jumps` | jump instructions, the final JVB included |
| `screen_bytes` | bytes of screen memory ANTIC fetches for the frame |
| `modes` | `{"2": n, ...}`: mode line count per ANTIC mode, upper-case hex keys, ascending |
| `fetch` | screen memory read, in order, as `"start-end"` (4 lower-case hex digits each, end exclusive); consecutive reads are merged |

## Layout

- `antickit/dlist.py` – display list walk
- `dlinfo.py` – command line

## Running

    python3 dlinfo.py examples/title.xlm examples/status.xlm
    python3 -m unittest discover -s tests
