# oledkit

Check for the SSD1306 bus logs (`.oled`) the team records from the emulator
while re-creating the 128 × 64 OLED screens of handheld games and gadgets. A
log lists every byte the CPU sent to the controller after reset;
`oledinfo.py` replays it and prints one JSON summary of the resulting picture
per log.

## File layout

`SSD1306` and a NUL, the number of writes (`u32` little-endian), then two
bytes per write: the D/C line (0 = command byte, 1 = GDDRAM data byte) and
the byte. Command bytes include the parameter bytes of multi-byte commands,
as the controller receives them.

The controller starts as after reset: display off, page addressing mode,
column and page 0, horizontal/vertical window columns 0–127 and pages 0–7,
start line 0, multiplex ratio 64, normal COM scan, contrast 0x7F, normal
(not inverted) display following RAM. Rows below the multiplex ratio stay
dark; reverse COM scan turns the rows that are shown upside down.

## Output

| field | meaning |
|---|---|
| `display_on`, `mode` | display on/off; `"horizontal"`, `"vertical"` or `"page"` |
| `pointer` | `[page, column]` the next data byte goes to |
| `window` | `columns` and `pages` ranges set for horizontal/vertical mode |
| `start_line`, `mux`, `com_reverse`, `contrast`, `inverse`, `entire_on` | display settings |
| `lit` | lit pixels in the 128 × 64 picture |
| `bbox` | `[x0, y0, x1, y1]` of lit pixels, or `null` |
| `picture_crc32` | CRC-32 of the picture as 8192 bytes (1 lit, 0 dark), row by row, lower-case hex |
| `ram_crc32` | CRC-32 of the 1024 GDDRAM bytes, page 0 column 0 first |

## Layout

- `oledkit/ssd1306.py` – command decoder, GDDRAM, picture
- `oledinfo.py` – command line

## Running

    python3 oledinfo.py examples/title.oled examples/hud.oled
    python3 -m unittest discover -s tests
