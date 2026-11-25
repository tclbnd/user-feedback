# anikit

Check for the animated cursor packs (`.ani`) bundled with the desktop themes
the design team ships. `aniinfo.py` prints one JSON summary per cursor, used
to check frame counts, playback order, timing and the frame images.

## Output

| field | meaning |
|---|---|
| `frames`, `steps` | frames stored and steps the animation plays (integers) |
| `sequence` | the frame index shown at each step; from the `seq ` chunk, else the frames in order |
| `default_rate` | the header's rate, in 1/60 s ticks, used for steps with no `rate` entry |
| `loop_seconds` | time for one pass through all steps in seconds as a fraction string; each step's `rate` entry (in 1/60 s) or the header rate |
| `icon_frames` | `true` when the header says the frames are icon resources |
| `title`, `artist` | from the INFO list (`INAM`, `IART`), `null` when missing |
| `frame_images` | per frame: `type` (`icon` or `cursor`), `width`, `height` (pixels), `bit_count`, `png` (`true` for a PNG image), `images` (images in the resource) and `hotspot` (`[x, y]` for cursors, else `null`) |

Zero width or height in an icon or cursor entry means 256 pixels. Chunks with an
odd length are followed by a pad byte, which the walker skips.

The RIFF structure, the anih header and its flags, the rate and seq chunks,
the INFO list and the ICO/CUR frames follow the Windows animated cursor and
icon formats.

## Layout

- `anikit/ani.py` – RIFF chunks, header, frames
- `aniinfo.py` – command line

## Running

    python3 aniinfo.py examples/busy.ani examples/hourglass.ani
    python3 -m unittest discover -s tests
