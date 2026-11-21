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
| `loops` | sampler loops: `cue`, `type` (`forward`, `alternating`, `backward`, `vendor`), `start`, `end`, `end_fraction` (fraction of a sample after `end`, fraction string), `length` (frames played per pass; `end - start + 1`), `play_count` (`null` = loop forever, i.e. `smpl` `dwPlayCount` 0) |
| `instrument` | from `inst`: `note`, `detune_cents` (signed), `gain_db` (signed), `notes` (low, high), `velocities` (low, high) |
| `info` | `LIST INFO` text fields |

Bytes after the end given by the RIFF header are ignored, and sub-chunks are
read on even (word-aligned) offsets, so an odd-length `labl`/`note` does not
shift the chunks that follow it.

For `cue ` points the data-chunk offset is taken from `dwSampleOffset`, falling
back to `dwPosition` when that is zero; markers are then sorted by position,
ties broken by cue ID. `smpl` pitch and loop fractions count in 1/2<sup>32</sup>
steps, and `dwSMPTEFormat` 29 is the 29.97 fps drop-frame rate, written with a
`;` before the frames.

## Layout

- `wavkit/riff.py` – chunk walk and chunk parsers
- `wavmarks.py` – command line

## Running

    python3 wavmarks.py examples/field_take.wav examples/pad_C3.wav
    python3 -m unittest discover -s tests
