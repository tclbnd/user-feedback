# s98kit

Catalogue check for the PC-98 and FM-chip music logs (`.s98`) in the
chip-music archive. `s98info.py` prints one JSON summary per log, used to
check the chips, running time, loop point and tags before conversion.

## Output

| field | meaning |
|---|---|
| `version` | the header version character (`1`, `2`, `3`) |
| `tick` | seconds per sync as a fraction string |
| `devices` | per sound chip: `type` (`ym2149`, `ym2203`, `ym2612`, `ym2608`, `ym2151`, `ym2413`, `ym3526`, `ym3812`, `ymf262`, `ay-3-8910`, `sn76489`, `none`, else `type_N`), `clock` (Hz) and `pan` (integers) |
| `writes` | register writes per device, in device order |
| `syncs`, `duration` | total syncs, and `syncs × tick` in seconds as a fraction string |
| `loop_seconds` | the time at which the loop point lies, `null` without one |
| `end_marker` | `true` when an end/loop command ends the data |
| `tags` | the tag block: `[S98]` tags by lower-case key, or for an old-style tag its text as `title`; `{}` without tags |

The header (timer values and their defaults, device list and its default),
the command set and the tag block follow the S98 format specification
(version 3, with versions 1 and 2).

## Layout

- `s98kit/s98.py` – header, commands, tags
- `s98info.py` – command line

## Running

    python3 s98info.py examples/field.s98 examples/opening.s98
    python3 -m unittest discover -s tests
