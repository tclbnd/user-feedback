# s3mkit

Inventory tool for the ScreamTracker 3 modules in the retro game soundtrack
collection. `s3minfo.py` prints one JSON summary per `.s3m` file.

## Output

| field | meaning |
|---|---|
| `title`, `tracker`, `tracker_version` | from the header (`Cwt/v`) |
| `signed_samples` | sample data is signed (`Ffi` = 1) |
| `stereo` | master volume bit 7 |
| `channels` | channels that are enabled in the channel settings |
| `orders` | the order list as played: marker entries are skipped and the list ends at the end marker |
| `patterns` | number of patterns in the header |
| `notes` | pattern cells that play a note (note cut and empty notes are not notes) |
| `samples` | sampled instruments (type 1) with `index` (instrument number), `name`, `frames`, `bits`, `stereo`, `loop`, `loop_start`, `loop_length` (frames per loop pass), `volume`, `c2spd` |
| `duration` | seconds to play the order list once, as a fraction string |

Timing follows ScreamTracker 3: a row lasts `speed` ticks of 2.5/tempo seconds;
`Axx` sets the speed, `Txx` the tempo, `Bxx` jumps to an order position and
`Cxx` breaks to row xx (decimal digits) of the next position. Playback ends
when a row would be played a second time or the order list runs out.

## Layout

- `s3mkit/s3m.py` – header, instruments, patterns, timing
- `s3minfo.py` – command line

## Running

    python3 s3minfo.py examples/menu.s3m examples/stage2.s3m
    python3 -m unittest discover -s tests
