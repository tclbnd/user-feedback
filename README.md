# scrkit

Check for the ZX Spectrum loading screens and title art in the retro game
archive (`.scr`). `scrinfo.py` prints one JSON summary per screen, used to
verify screens before they go into the emulator front-end and the web gallery.

## Output

| field | meaning |
|---|---|
| `format` | `standard` (6912 bytes), `ulaplus` (6976: with a 64-byte ULAplus palette), `timex_hicolor` (12288) or `timex_hires` (12289) |
| `width`, `height` | in pixels |
| `ink_pixels` | the number of set bitmap bits |
| `points` | the displayed colour (`#rrggbb`) at `top_left` (0, 0), `top_right` (width − 1, 0), `bottom_left` (0, 191), `bottom_right` (width − 1, 191), `center` (width / 2, 96) and `probe` (width × 100 / 256, 70) |
| `flashing_cells`, `bright_cells` | the number of attribute cells that flash and that are shown in bright colours, as the display shows them (0 for hi-res) |
| `mode` | the hi-res mode byte, only for `timex_hires` |

Colours: Spectrum colour numbers 0–7 are black, blue, red, magenta, green,
cyan, yellow and white; a component is 0xD7 normally and 0xFF with the bright
bit (black stays black). ULAplus colours are GRB 3-3-2, widened by repeating
their bits; hi-res screens take their ink and paper colours from the mode byte (no bright).

The display file layout, attribute bytes, Timex screen modes and the ULAplus
palette follow the ZX Spectrum and Timex hardware.

## Layout

- `scrkit/scr.py` – layouts, attributes, colours
- `scrinfo.py` – command line

## Running

    python3 scrinfo.py examples/loading.scr examples/title_hicolor.scr
    python3 -m unittest discover -s tests
