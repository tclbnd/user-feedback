# scckit

Caption check for the broadcast caption files (Scenarist `.scc`, CEA-608 line 21
data, field 1) that come with the series deliverables. `sccinfo.py` decodes each
file and prints one JSON summary, used to catch captions that come up at the
wrong time or with broken text.

## Output

| field | meaning |
|---|---|
| `drop_frame` | the file uses drop-frame timecode (`;` before the frames) |
| `words` | byte pairs in the file |
| `bad_parity_bytes` | bytes whose odd parity bit is wrong |
| `duration` | seconds from 00:00:00:00 to the end of the last byte pair, as a fraction string |
| `redundant_codes` | repeats of doubled control codes (the second of two identical control-code pairs in a row, which the decoder ignores) |
| `CC1`, `CC2` | pop-on captions of each caption channel, in the order they appear |

Each caption has `start` and `end` (seconds, fraction strings; `end` is `null`
when the caption is still on screen at the end of the file), `rows` (screen
rows 1–15 of its lines), `columns` (start column 0–31 of each line: the indent of
its preamble address code plus any Tab Offsets sent before the line's first
character; a Tab Offset after text skips that many columns, shown as spaces)
and `text` (its lines joined by `\n`).

Every byte pair takes one frame: the timecode of a line is the frame of its
first pair, and the pairs after it follow one frame apart. The video is
29.97 frames per second for both drop-frame and non-drop-frame timecode.
A caption starts when End Of Caption puts it on screen and ends when it is
erased or replaced. Only pop-on captions are reported.

## Layout

- `scckit/scc.py` – line parsing, CEA-608 decoding, caption memories
- `sccinfo.py` – command line

## Running

    python3 sccinfo.py examples/episode.scc examples/promo.scc
    python3 -m unittest discover -s tests
