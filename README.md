# itkit

Catalogue check for the tracker-music archive (Impulse Tracker `.it`
modules). `itinfo.py` prints one JSON summary per module, used to check song
order, channels, samples and patterns before modules go on the player.

## Output

| field | meaning |
|---|---|
| `title` | the song name (code page 437, trailing spaces removed), `null` when empty |
| `tracker`, `compatible` | the "created with" and "compatible with" tracker versions as `major.minor`, e.g. `2.14` |
| `stereo`, `instrument_mode`, `linear_slides` | the header flags (`true`/`false`) |
| `global_volume`, `mix_volume`, `speed`, `tempo` | integers from the header |
| `orders` | the patterns the song plays, in order, up to the end-of-song marker (separator markers left out) |
| `channels` | the number of enabled channels |
| `surround_channels` | the number of channels panned to surround |
| `instruments` | the number of instruments (an integer) |
| `samples` | one entry per sample, see below |
| `pattern_rows` | the number of rows of each pattern (integers) |
| `notes`, `note_offs`, `note_cuts` | totals over all patterns of the note events in the stored pattern data within each pattern's rows: notes 0–119, note-offs and note-cuts (one integer each) |
| `message` | the song message with line breaks as `\n`, `null` without one |

Each sample has `name`, `length` (in sample frames), `bits` (8 or 16),
`stereo`, `c5_speed` (Hz), `loop` and `sustain_loop` (`forward`, `pingpong`
or `null`), `compressed`, and `data_bytes` (the size of an uncompressed
sample's data, `null` for compressed samples or samples without data).

The header, order list markers, channel panning values, sample flags,
pattern headers and the song message follow the Impulse Tracker format.
Both the 64-byte IT 2.00 and 80-byte IT 2.04+ sample header layouts are
recognised, as are pattern blocks with and without the `IMPP` marker.

## Layout

- `itkit/it.py` – header, orders, channels, samples, packed patterns, message
- `itinfo.py` – command line

## Running

    python3 itinfo.py examples/debris.it examples/ambient.it
    python3 -m unittest discover -s tests
