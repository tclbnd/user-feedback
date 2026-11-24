# ttmlkit

Check for the TTML subtitle files (W3C TTML1 and the IMSC1 profiles) that
come with programme deliveries. `ttmlinfo.py` reads each document and prints
one JSON summary, used to compare subtitle timing with the programme.

## Output

| field | meaning |
|---|---|
| `frame_rate` | the effective frame rate (`ttp:frameRate` times `ttp:frameRateMultiplier`) as a fraction string |
| `tick_rate` | the tick rate as a fraction string (`ttp:tickRate`; without one, the effective frame rate times `ttp:subFrameRate` when `ttp:frameRate` is given, else 1) |
| `language` | `xml:lang` of the root element |
| `paragraphs` | one entry per `p` element, sorted by begin time |
| `duration` | the latest paragraph end in seconds as a fraction string (`0` without paragraphs) |

Each paragraph has `begin` and `end` (seconds on the document timeline as
fraction strings; `end` is `null` when nothing ends it), `lines` (its text
lines: a `br` starts a new line, runs of white space collapse to one space)
and `region` (the `region` attribute, `null` without one).

Times follow TTML1: clock times `hh:mm:ss`, `hh:mm:ss.fraction` and
`hh:mm:ss:frames[.subframes]`, offset times with the units `h`, `m`, `s`,
`ms`, `f` (frames) and `t` (ticks). Frames are counted at the effective frame
rate (so a `1000 1001` multiplier is honoured), and the `frames` part of a
clock time is a frame count, never hundredths of a second. `begin` and `end`
count from the time the element's container starts (in a `seq` container, from
the end of the previous sibling); `dur` counts from the element's own begin,
and when both `end` and `dur` are given the earlier end wins. A child never
ends after its parent. In a `par` container an element without `end` or `dur`
lasts until its parent ends; in a `seq` container it takes no time.

## Layout

- `ttmlkit/ttml.py` – parameters, time expressions, timing, paragraphs
- `ttmlinfo.py` – command line

## Running

    python3 ttmlinfo.py examples/episode.ttml examples/promo.ttml
    python3 -m unittest discover -s tests
