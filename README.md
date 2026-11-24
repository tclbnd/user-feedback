# ttxkit

Check for the teletext captures (`.t42`, 42 bytes per packet) recovered from
the broadcaster's off-air tape archive. `ttxinfo.py` reads each capture and
prints one JSON summary per file, used to find subtitle pages and judge how
clean the recovery was.

## Output

| field | meaning |
|---|---|
| `packets` | the number of 42-byte packets |
| `magazines` | magazine numbers (1–8) seen in decodable packet addresses, sorted |
| `pages` | one entry per decodable page header packet, in file order |
| `display_rows` | the number of decodable packets for rows 1–24 |
| `extension_packets` | the number of decodable packets for rows 25–31 |
| `header_text` | the 32 characters of the first page header that is not a time-filling header (page `xFF`), trailing spaces removed; `null` without one |
| `corrected` | the number of Hamming 8/4 bytes that had a correctable error |
| `hamming_errors` | the number of Hamming 8/4 bytes that could not be decoded (a packet with such an address or header byte is not counted further) |
| `parity_errors` | the number of text bytes with a parity error (shown as a space) |

Each page has `page` (magazine digit then the two page-number digits in
hexadecimal, e.g. `100`, `8FF`), `subcode` (four hexadecimal digits S4 S3 S2
S1), `erase`, `newsflash`, `subtitle`, `suppress_header` and `magazine_serial`
(the control bits C4, C5, C6, C7 and C11, `true`/`false`).

Addresses, page headers and text bytes follow ETSI EN 300 706 (Hamming 8/4 for
address and header fields, odd parity for text).

## Layout

- `ttxkit/ttx.py` – Hamming 8/4, packet addresses, page headers, text
- `ttxinfo.py` – command line

## Running

    python3 ttxinfo.py examples/bbc1_1987.t42 examples/subtitles.t42
    python3 -m unittest discover -s tests
