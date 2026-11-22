# apngkit

Asset check for the animated PNG (APNG) stickers and UI animations in the app.
`apnginfo.py` reads each file and prints one JSON summary, used to catch
broken animations and to report play time.

## Output

| field | meaning |
|---|---|
| `width`, `height` | canvas size from IHDR |
| `animated` | the file is an APNG (a plain PNG viewer shows only the default image) |
| `declared_frames`, `frames` | frame count stated in acTL and frames actually present (one per fcTL) |
| `plays` | number of loops, `null` for endless looping |
| `default_image_in_animation` | the default image (IDAT) is the first frame of the animation |
| `delays`, `duration` | per-frame delays and total play time of one loop, in seconds as fraction strings |
| `dispose`, `blend` | per frame, as the frame is rendered: `none` / `background` / `previous`, and `source` / `over` |
| `sequence_ok` | fcTL and fdAT sequence numbers run 0, 1, 2, … without gaps |
| `regions_ok` | every frame region is non-empty and lies inside the canvas |
| `crc_errors` | chunks whose CRC does not match |

For a file that is not animated the animation fields are `null`, empty or 0.

## Layout

- `apngkit/apng.py` – chunks, frame control
- `apnginfo.py` – command line

## Running

    python3 apnginfo.py examples/loader.png examples/badge.png
    python3 -m unittest discover -s tests
