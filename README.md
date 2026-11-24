# idxkit

Check for the VobSub subtitle tracks (`.idx` + `.sub`) ripped from the DVD
catalogue before conversion. `idxinfo.py` reads each index file and prints one
JSON summary per file.

## Output

| field | meaning |
|---|---|
| `version` | the version number in the first line (an integer), `null` without one |
| `size` | `[width, height]` of the subtitle canvas |
| `origin`, `scale` | `[x, y]` from `org` and `[h, v]` percentages from `scale` (integers) |
| `alpha` | opacity percentage (an integer) |
| `palette` | the 16 palette colours as `#rrggbb` (lower case) |
| `custom_colors` | the four custom colours as `#rrggbb` when custom colours are switched on, else `null` |
| `forced` | `true` when the `forced subs` setting is `ON` |
| `default_language` | `langidx` (an integer), `null` without one |
| `languages` | one entry per `id` line in file order |

Each language has `id` (the language code), `index` (the stream index the
`id` line gives), `subtitles` (the number of timestamp lines), `first` and
`last` (times of its first and last subtitle in seconds as fraction strings,
`null` without subtitles), `delays` (the number of delay lines) and
`backwards` (subtitles whose time is earlier than the subtitle before them).

Times follow the VobSub index format. The header's `time offset` (milliseconds)
applies to every timestamp; `delay` lines shift the timestamps that come after
them in the same language stream, adding up.

## Layout

- `idxkit/idx.py` – header settings, languages, timestamps
- `idxinfo.py` – command line

## Running

    python3 idxinfo.py examples/feature.idx examples/extras.idx
    python3 -m unittest discover -s tests
