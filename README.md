# sf2kit

Inventory tool for the SoundFont banks shipped with the game music engine.
`sf2info.py` reads SoundFont 2 files (`.sf2`) and prints one JSON summary per
bank so the music team can see which presets use which instruments and
samples.

## Output

| field | meaning |
|---|---|
| `name`, `version` | bank name (INAM) and SoundFont version (ifil, `major.minor` with two-digit minor, e.g. `2.01`) |
| `sample_data_frames` | 16-bit sample frames in the smpl chunk |
| `has_24bit` | whether an sm24 chunk is present |
| `presets` | `name`, `bank`, `preset`, `zones` (excluding a global zone), `global_zone`, `instruments` (names, one per zone that refers to an instrument) |
| `instruments` | `name`, `zones` (excluding a global zone), `global_zone`, `samples` (names), `key_ranges` (`lo-hi` per zone, `0-127` when the zone has no key range) |
| `samples` | `name`, `frames`, `sample_rate`, `loop_start` and `loop_end` (relative to the sample start), `root_key`, `correction` (cents), `type` |

Terminal records of the preset, instrument and sample lists are not reported.

## Layout

- `sf2kit/riff.py` – RIFF and LIST walk
- `sf2kit/hydra.py` – presets, instruments, zones, generators, sample headers
- `sf2kit/info.py` – summary
- `sf2info.py` – command line

## Running

    python3 sf2info.py examples/studio.sf2 examples/drums.sf2
    python3 -m unittest discover -s tests
