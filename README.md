# yjkkit

Check for MSX2 / MSX2+ bitmap screens dumped from the art team's emulator
(`.v99`). `yjkinfo.py` prints one JSON summary per dump with the palette
colours in use, the most common colours and the first pixels, so a converted
picture can be compared with what the VDP puts on screen.

## File layout

`V9958`, the BASIC screen mode the picture uses (`8`, `10`, `11` or `12`), the
16 palette registers as the VDP stores them (two bytes each), then the
256 × 212 bytes of the bitmap, top line first, exactly as they sit in VRAM.
A dump cut short keeps its whole lines only. Pixels are shown the way the
V9938/V9958 displays them in that mode.

## Output

| field | meaning |
|---|---|
| `mode` | screen mode byte |
| `lines`, `complete` | whole lines in the dump; `true` for all 212 |
| `colors_used` | distinct displayed colours |
| `top_colors` | the 5 most frequent displayed colours as `[["#rrggbb", n], ...]`, most frequent first, ties by colour string |
| `palette_pixels` | pixels shown from the palette rather than computed from their own colour data |
| `first_pixels` | the first 16 pixels of the top line as `#rrggbb` |

Colour components are widened to 8 bits as `round(c × 255 / max)` from the
VDP's own component range.

## Layout

- `yjkkit/vram.py` – screen decoding
- `yjkinfo.py` – command line

## Running

    python3 yjkinfo.py examples/photo.v99 examples/title.v99
    python3 -m unittest discover -s tests
