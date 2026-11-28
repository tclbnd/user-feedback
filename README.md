# kgvkit

Frame check for Kega Game Video recordings (KGV1, the AVI codec the Kega
Sega emulators use for gameplay captures). The rip tool stores the KGV1
frames of one capture as a `.kgv` pack; `kgvinfo.py` decodes every frame the
way the KGV1 codec does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `KGV1PACK` |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the frame exactly as stored in the AVI |

Each frame starts with two bytes giving its size, followed by little-endian
16-bit codes: RGB555 pixels, back references into the frame being decoded,
and copies from the previous frame through eight offset slots.

## Output

| field | meaning |
|---|---|
| `frames` | one object per frame: `state` (`decoded`, or `error` when the codec rejects the frame), `width`, `height` and `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom, 5-bit components widened to 8 bits by repeating their top bits (`null` for an error) |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `kgvkit/kgv.py` – pack reader and frame decoder
- `kgvinfo.py` – command line

## Running

    python3 kgvinfo.py examples/demo.kgv
    python3 -m unittest discover -s tests
