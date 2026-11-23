# supkit

Subtitle check for the Blu-ray subtitle tracks (PGS, extracted as `.sup`) of
the film library. `supinfo.py` reads each stream and prints one JSON summary,
used to catch subtitles that come up at the wrong time, never go away, or have
a broken picture.

## Output

| field | meaning |
|---|---|
| `width`, `height` | video size from the first presentation composition segment |
| `display_sets` | display sets in the stream (one presentation composition segment each) |
| `epochs` | display sets that start a new epoch |
| `subtitles` | subtitles in the order they appear on screen |

Each subtitle has `start` and `end` (seconds, fraction strings; `end` is `null`
when nothing takes it off screen), `forced` (any of its objects is a forced
subtitle) and `objects`: per composition object its `id`, position `x`, `y`,
the object's `width` and `height`, and `opaque_pixels` (pixels of the decoded
bitmap whose palette entry is not fully transparent; entries the palette does
not define are transparent), `window` (the window id it is composed in) and
`crop` (`x`, `y`, `width`, `height` of the cropped part when the composition
crops the object, else `null`). A subtitle also has `windows` (the windows the
epoch's latest window definition segment defines: `id`, `x`, `y`, `width`,
`height`) and `palette_updates` (palette-only display sets while it is on
screen).

A subtitle starts when a display set puts objects on screen and ends when
another display set replaces or removes them. A display set that only changes
the palette (fades) does not start a new subtitle.

## Layout

- `supkit/sup.py` – segments, display sets, object and palette decoding
- `supinfo.py` – command line

## Running

    python3 supinfo.py examples/feature.sup examples/trailer.sup
    python3 -m unittest discover -s tests
