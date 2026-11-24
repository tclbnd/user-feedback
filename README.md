# asskit

QC check for the styled subtitle scripts (`.ass` and older `.ssa`) that come
with fansub-licensed and anime titles. `assinfo.py` prints one JSON summary
per script, used to check timing, styles and positioning before encoding.

## Output

| field | meaning |
|---|---|
| `script_type` | `ScriptType` from `[Script Info]`, `null` without one |
| `play_res` | `[PlayResX, PlayResY]` (integers, `null` when missing) |
| `styles` | by style name: `font`, `size` (a number), `color` (the primary colour as `#rrggbb`), `opacity` (0 transparent – 255 opaque) and `alignment` (numpad position 1–9) |
| `events` | one entry per `Dialogue` line, in file order |
| `comments` | the number of `Comment` lines (they are not events) |
| `duration` | the latest event end in seconds as a fraction string |

Each event has `start`, `end` (seconds as fraction strings), `style` (without
a leading `*`), `layer` (an integer, 0 when the format has none), `lines`
(the text split at `\N`, override blocks and drawings removed, `\h` as a
space, `\n` a line break only with `WrapStyle: 2` and otherwise a space, runs
of spaces collapsed), `alignment` (numpad 1–9: an `\an` tag, else an `\a`
tag, else the style's), `position` (`[x, y]` from `\pos`, numbers, else
`null`), `fade` (`[in, out]` milliseconds from `\fad`, else `null`) and
`karaoke` (the total of `\k`, `\K`, `\kf` and `\ko` durations in seconds as a
fraction string, `null` without karaoke tags).

Section names, `Format` lines, times, colours, the SSA v4 alignment
numbering and override tags follow the SubStation Alpha v4 and v4+
specifications.

## Layout

- `asskit/ass.py` – sections, styles, events, override tags
- `assinfo.py` – command line

## Running

    python3 assinfo.py examples/episode01.ass examples/classic.ssa
    python3 -m unittest discover -s tests
