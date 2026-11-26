# pvrtex

Check for the Dreamcast PVR textures (`.pvr`) the art team extracts from disc
images while remastering. `pvrinfo.py` prints one JSON summary per texture with
the texel colours as the PowerVR2 samples them.

## File layout

An optional `GBIX` chunk (global index), then the `PVRT` chunk: chunk size,
pixel format byte, data format byte, two pad bytes, width and height
(little-endian), then the texture data. Pixel formats 0 (ARGB1555), 1 (RGB565)
and 2 (ARGB4444) and data formats 1 (twiddled), 3 (VQ) and 9 (rectangle) occur
in these files, without mipmaps; anything else is an error. Twiddled and VQ
textures must be square.

## Output

| field | meaning |
|---|---|
| `pixel_format` | `"ARGB1555"`, `"RGB565"` or `"ARGB4444"` |
| `data_format` | `"twiddled"`, `"vq"` or `"rectangle"` |
| `width`, `height` | texture size |
| `global_index` | the GBIX value, or `null` without a GBIX chunk |
| `complete` | `true` when all texture data is in the file |
| `vq_codes` | distinct codebook entries the texture uses (VQ only, else `null`) |
| `colors_used` | distinct texel colours |
| `transparent_pixels` | texels with alpha 0 |
| `first_row`, `last_row` | top and bottom texel rows as runs `[["#rrggbbaa", count], ...]` |

Components narrower than 8 bits are widened by repeating their top bits
(4-bit values times 17). Texel fields are `null` when data is missing or the
size is zero.

## Layout

- `pvrtex/pvr.py` – chunks, formats, texel decoding
- `pvrinfo.py` – command line

## Running

    python3 pvrinfo.py examples/sign.pvr examples/road.pvr
    python3 -m unittest discover -s tests
