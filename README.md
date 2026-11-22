# flickit

Asset report for the cut-scene and menu animations of the game remaster, kept
as Autodesk FLIC files (`.fli` from Animator, `.flc` from Animator Pro and
later tools). `flicinfo.py` reads each file and prints one JSON summary.

## Output

| field | meaning |
|---|---|
| `format` | `FLI` or `FLC` |
| `width`, `height`, `depth` | from the file header |
| `frames` | frames of the animation, not counting the ring frame |
| `ring_frame` | the file ends with a ring frame (the delta from the last frame back to the first, for looping) |
| `frame_delay_ms` | default delay between frames in milliseconds, as a fraction string |
| `duration` | playing time of the animation in seconds as a fraction string; a frame whose header gives its own delay uses that delay |
| `empty_frames` | frames without sub-chunks (the picture does not change) |
| `prefix` | the file has a prefix chunk before the first frame |
| `chunks` | sub-chunk counts by type over all frame chunks, ring frame included: `COLOR_256`, `COLOR_64`, `DELTA_FLC`, `DELTA_FLI`, `BLACK`, `BYTE_RUN`, `FLI_COPY`, `PSTAMP`, `LABEL`, else `type_N` |
| `first_colors` | palette entries 0–3 after the first frame as `#rrggbb` (8 bits per component), `null` when not set |

## Layout

- `flickit/flic.py` – header and chunks
- `flicinfo.py` – command line

## Running

    python3 flicinfo.py examples/intro.flc examples/menu.fli
    python3 -m unittest discover -s tests
