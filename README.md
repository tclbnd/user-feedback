# wavkit

Marker export for the sound library (WAV takes from field recorders, edited in
the studio editors, and sampler instruments). `wavmarks.py` prints one JSON
summary per file.

## Output

| field | meaning |
|---|---|
| `channels`, `sample_rate`, `block_align`, `bits`, `frames` | from `fmt ` and `data` |
| `markers` | cue points ordered by sample position (ties by cue ID): `id`, `sample` (frame offset in the data chunk), `time` (seconds, fraction string), `label`, `note`, and `length` for points that have a region (`ltxt`) |
| `playlist` | `plst` entries: `cue`, `length`, `repeats` |
| `sampler` | from `smpl`: `manufacturer` (MIDI manufacturer ID as hex bytes, e.g. `41` or `00 20 33`; `null` if none), `product`, `midi_note`, `pitch_fraction` (fraction of a semitone above the note, fraction string), `sample_period_ns`, `smpte_offset` (`hh:mm:ss:ff`, `;` before the frames for 29.97 drop-frame, `null` without SMPTE format); `null` without `smpl` |
| `loops` | sampler loops: `cue`, `type` (`forward`, `alternating`, `backward`, `vendor`), `start`, `end`, `end_fraction` (fraction of a sample after `end`, fraction string), `length` (frames played per pass), `play_count` (`null` = loop forever) |
| `instrument` | from `inst`: `note`, `detune_cents`, `gain_db`, `notes` (low, high), `velocities` (low, high) |
| `info` | `LIST INFO` text fields |

Bytes after the end given by the RIFF header are ignored.

## Layout

- `wavkit/riff.py` – chunk walk and chunk parsers
- `wavmarks.py` – command line

## Running

    python3 wavmarks.py examples/field_take.wav examples/pad_C3.wav
    python3 -m unittest discover -s tests
