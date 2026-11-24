# lrckit

Check for the synchronised lyrics (`.lrc`) delivered with the karaoke and
streaming catalogue. `lrcinfo.py` prints one JSON summary per file, used to
check line timing, tags and word-level timing before the lyrics go live.

## Output

| field | meaning |
|---|---|
| `title`, `artist`, `album`, `author`, `created_by` | the `ti`, `ar`, `al`, `au` and `by` tags, `null` when missing or empty |
| `length` | the `length` tag (`mm:ss` or `mm:ss.x…`) in seconds as a fraction string, `null` without a valid one |
| `offset_ms` | the `offset` tag (an integer, 0 without one) |
| `lines` | one entry per time tag, sorted by time (lines with equal times keep file order) |
| `blank_lines` | the number of entries without text (they clear the screen) |
| `enhanced` | `true` when any line carries word time tags |

Each line entry has `time` (seconds as a fraction string), `text` (the lyric
with word time tags removed and runs of spaces collapsed, `null` when empty)
and `words` (the word time tags of that line in seconds, fraction strings).

Time tags are `[mm:ss]`, `[mm:ss.x]`, `[mm:ss.xx]`, `[mm:ss.xxx]` or
`[mm:ss:xx]`; minutes may exceed 59. A line may start with several time tags
and is shown at each of them. Word tags are `<mm:ss.xx>` (same forms). The
offset applies to every time, line and word, as LRC players apply it; times
that end up below 0 are 0.

## Layout

- `lrckit/lrc.py` – tags, time tags, offset
- `lrcinfo.py` – command line

## Running

    python3 lrcinfo.py examples/harbour.lrc examples/karaoke.lrc
    python3 -m unittest discover -s tests
