# svxkit

Inventory tool for the Amiga instrument library used by the game music team.
`svxinfo.py` reads IFF 8SVX sound files and prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `name`, `author`, `annotations` | from the NAME, AUTH and ANNO chunks (all ANNO chunks in order) |
| `sample_rate` | samples per second of the highest octave |
| `octaves`, `one_shot`, `repeat` | from VHDR (one-shot and repeat part of the highest octave, in samples) |
| `samples_per_octave` | samples of each stored octave, highest octave first |
| `compression` | `none` or `fibonacci` |
| `volume` | playback volume, 1.0 = full |
| `channels`, `channel` | 1 or 2; `mono`, `left`, `right` or `stereo` |
| `frames` | decoded samples per channel in the BODY |
| `duration` | seconds of the highest octave (one-shot plus repeat part), as a fraction |
| `peak` | per channel, largest absolute decoded sample |
| `head` | per channel, the first 4 decoded samples |

## Layout

- `svxkit/svx.py` – IFF chunks, VHDR, BODY decoding and summary
- `svxinfo.py` – command line

## Running

    python3 svxinfo.py examples/bass.8svx examples/strings.8svx
    python3 -m unittest discover -s tests
