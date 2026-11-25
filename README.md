# wadkit

Check for the texture packages (`.wad`) the level team collects from Quake and
Half-Life mods. `wadinfo.py` prints one JSON summary per package, so a
package can be checked before its textures are converted.

## Input

Packages exactly as the mod tools write them: Quake `WAD2` and Half-Life
`WAD3`, holding mip textures, pictures, palettes and fonts. WAD2 textures and
pictures take their colours from the package's palette lump named `PALETTE`;
WAD3 textures and pictures carry their own palette.

## Output

| field | meaning |
|---|---|
| `version` | `"WAD2"` or `"WAD3"` |
| `lump_count` | number of lumps in the directory |
| `complete` | `true` when every directory entry and lump lies inside the file |
| `lumps` | one object per directory entry, in directory order |

Each lump object:

| field | meaning |
|---|---|
| `name` | lump name up to the first NUL |
| `type` | `"miptex"`, `"qpic"`, `"palette"`, `"font"` or `"other"` |
| `width`, `height` | image size for mip textures and pictures, otherwise `null` |
| `mip_sizes` | mip textures only: pixel bytes of mip levels 0–3 (4 integers), otherwise `null` |
| `first_pixel` | colour of the top-left pixel of the full-size image, `"#rrggbb"` lower case; `null` when the pixels or the palette are not in the package |
| `smallest_pixel` | mip textures only: colour of the bottom-right pixel of mip level 3, same notation |
| `transparent` | `true` for a mip texture whose name marks it as transparent |
| `transparent_pixels` | for such a texture, how many full-size pixels use the transparent index; `0` otherwise |
| `animation` | for an animated mip texture, `{"frame": n, "alternate": false/true}` from its name prefix; `null` otherwise |

## Layout

- `wadkit/wad.py` – directory, lumps, palettes
- `wadinfo.py` – command line

## Running

    python3 wadinfo.py examples/base.wad examples/halflife.wad
    python3 -m unittest discover -s tests
