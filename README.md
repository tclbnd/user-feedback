# gifkit

Check for the animated GIF stickers and banners in the studio's asset
library. `gifinfo.py` reads each GIF and prints one JSON summary per file, used
to confirm sizes, timing and looping before the assets ship.

## Output

| field | meaning |
|---|---|
| `version` | `87a` or `89a` |
| `width`, `height` | logical screen size |
| `global_colors` | the number of entries in the global colour table, `null` without one |
| `background` | background colour index (an integer) |
| `aspect` | pixel aspect ratio from the screen descriptor, `%.3f`, `null` when the byte is 0 |
| `loop_count` | the loop count of the looping application extension (an integer, 0 meaning forever), `null` without one |
| `frames` | one entry per image, in file order |
| `duration` | sum of the frame delays in seconds, as a fraction string |
| `comments` | comment extensions in file order (a list of strings, ISO 8859-1) |
| `truncated` | `true` when the file ends before the trailer |

Each frame has `x`, `y`, `width`, `height`, `interlaced` (`true`/`false`),
`local_colors` (the number of entries in its local colour table, `null`
without one), `delay` (the delay of its graphic control extension in hundredths
of a second, an integer; 0 without one), `disposal` (`none`, `keep`,
`background`, `previous`, else `type_N`) and `transparent` (the transparent
colour index, `null` when the image has no transparent colour).

Blocks, extensions and their scope follow the GIF89a specification; looping
application extensions from the browsers that defined them are in use.

## Layout

- `gifkit/gif.py` – screen, colour tables, extensions, images
- `gifinfo.py` – command line

## Running

    python3 gifinfo.py examples/spinner.gif examples/banner.gif
    python3 -m unittest discover -s tests
