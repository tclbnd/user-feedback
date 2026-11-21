# xmkit

Inventory tool for the soundtrack modules of the game's chiptune mode.
`xminfo.py` reads FastTracker II extended modules (`.xm`) and prints one JSON
summary per file.

## Output

| field | meaning |
|---|---|
| `name`, `tracker`, `version` | from the module header (`version` as `major.minor`, e.g. `1.04`) |
| `channels`, `song_length`, `restart` | from the module header |
| `frequency_table` | `linear` or `amiga` |
| `patterns` | number of rows of each stored pattern, in file order |
| `notes` | pattern cells that play a note (1–96; key-off is not a note) |
| `instruments` | `name` and `samples`, each with `name`, `frames` (sample frames, not bytes), `bits`, `loop` (`none`, `forward`, `pingpong`), `loop_start` and `loop_length` in frames, `volume`, `finetune`, `relative_note`, `peak` (largest absolute sample value, on the sample's own 8- or 16-bit scale) |
| `duration` | seconds to play the order list once, as a fraction string |

Timing follows FastTracker II: a row lasts `speed` ticks of 2.5/BPM seconds;
`Fxx` below 32 sets the speed and from 32 up sets the BPM; `Bxx` jumps to an
order position and `Dxx` breaks to row xx (written in decimal digits) of the
next position; `E6x` is the pattern loop (`E60` marks the loop start in that
channel, `E6x` plays back to it x more times) and `EEx` the pattern delay (the
row lasts x rows longer). Playback ends when a row would be played a second time or the
order list runs out.

## Layout

- `xmkit/xm.py` – header, patterns, instruments, timing
- `xminfo.py` – command line

## Running

    python3 xminfo.py examples/title.xm examples/boss.xm
    python3 -m unittest discover -s tests
