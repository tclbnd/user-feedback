# degaskit

Picture check for Atari ST artwork saved with Degas and Degas Elite. A game
studio is moving its old ST title screens and sprite sheets into a new art
pipeline; `degasinfo.py` decodes each picture the way the ST shows it and
prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 2 | resolution word (`u16` big-endian): 0 low (320 × 200, 16 colours), 1 medium (640 × 200, 4 colours), 2 high (640 × 400, monochrome); bit 15 set for a compressed (Degas Elite) picture |
| 32 | 16 palette words (`u16` big-endian), STE colour words |
| | picture data, uncompressed or compressed |
| 32 | compressed pictures only: Degas Elite colour-animation tables, four channels of first colour, last colour, direction and delay (`u16` big-endian, each table four words) |

## Output

| field | meaning |
|---|---|
| `width`, `height`, `planes` | picture size and bitplane count from the resolution |
| `compressed` | whether the picture is compressed |
| `palette` | the palette words in use, three lower-case hex digits each (`null` for monochrome) |
| `crc32` | CRC-32 (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom, colours as an STE displays them; monochrome pictures are white where the bit is 0 and black where it is 1 |
| `first_row`, `last_row` | CRC-32 of the first and of the last row alone, same byte layout |
| `animation` | compressed pictures: the four colour-animation channels (`first`, `last`, `direction`, `delay`); `null` otherwise |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `degaskit/degas.py` – picture decoder
- `degasinfo.py` – command line

## Running

    python3 degasinfo.py examples/title.pc1 examples/sheet.pi2
    python3 -m unittest discover -s tests
