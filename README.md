# stlkit

Check for the EBU STL subtitle files (`.stl`) delivered with programmes for
broadcast. `stlinfo.py` reads each file and prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `frame_rate` | 25 or 30, from the disk format code (an integer) |
| `code_page`, `language` | the GSI code page number and language code (text) |
| `character_table` | `latin`, `cyrillic`, `arabic`, `greek`, `hebrew` from the character code table, else `table_NN` |
| `programme`, `episode` | original programme and episode titles, trailing spaces removed (`null` when blank) |
| `declared_blocks`, `declared_subtitles` | TNB and TNS from the GSI block (integers) |
| `start_timecode` | TCP from the GSI block as `HH:MM:SS:FF` |
| `tti_blocks` | the number of 128-byte TTI blocks in the file |
| `comments` | the number of subtitles marked as comments |
| `user_data_blocks` | the number of TTI blocks carrying user data |
| `subtitles` | one entry per subtitle (comments not included), in file order |

Each subtitle has `number` (the subtitle number), `in` and `out` (`HH:MM:SS:FF`),
`duration` (seconds as a fraction string), `vertical` (the vertical position),
`justification` (`unchanged`, `left`, `centre`, `right`, else `code_N`) and
`lines` (its text, one string per row, empty rows left out, display control
codes removed).

Blocks, extension blocks, control codes and character tables follow EBU Tech
3264.

## Layout

- `stlkit/stl.py` – GSI block, TTI blocks, text decoding
- `stlinfo.py` – command line

## Running

    python3 stlinfo.py examples/news_fr.stl examples/doc_ru.stl
    python3 -m unittest discover -s tests
