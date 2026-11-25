# cartkit

Check for PICO-8 cartridges in their PNG form (`.p8.png`) collected for the
game jam archive. `cartinfo.py` prints one JSON summary per cartridge so the
archive team can spot broken or empty carts without opening each one in the
editor.

## File layout

A `.p8.png` is the 160 × 205 RGBA label image PICO-8 writes on export. Each
pixel carries one byte of the cartridge in the low two bits of its channels
(`A` bits 7–6, `R` bits 5–4, `G` bits 3–2, `B` bits 1–0), pixels row by row.
The bytes form the cartridge memory as PICO-8 lays it out: sprite sheet,
map, sprite flags, music, sound effects, compressed code, then the version
byte at `0x8000`.

## Output

| field | meaning |
|---|---|
| `version` | cartridge version byte |
| `gfx_histogram` | 16 integers: pixels of the 128 × 128 sprite sheet per colour |
| `flagged_sprites` | sprites with any flag set |
| `map_cells`, `map_tiles` | non-zero cells of the whole 128 × 64 map, and how many distinct non-zero tiles they use |
| `sfx_used` | sound effects with at least one audible note (volume above 0) |
| `notes` | audible notes over all sound effects |
| `waveforms` | 8 integers: audible notes per built-in waveform 0–7 (custom-instrument notes not included) |
| `custom_notes` | audible notes played with a custom instrument |
| `effects` | 8 integers: audible notes per effect 0–7 |
| `pitch_range` | `[lowest, highest]` pitch of the audible notes, `null` when there are none |
| `looping_sfx` | sound effects whose loop end is after their loop start |
| `patterns` | music patterns with at least one enabled channel |
| `music_sfx` | sorted sound effect numbers played by enabled channels |
| `loop_starts`, `loop_ends`, `stops` | music patterns carrying each of the three pattern flags |

## Layout

- `cartkit/cart.py` – PNG reading and cartridge memory
- `cartinfo.py` – command line

## Running

    python3 cartinfo.py examples/jam.p8.png examples/demo.p8.png
    python3 -m unittest discover -s tests
