# cmfkit

Catalogue check for the Sound Blaster FM music (`.cmf`, Creative Music Files)
in the DOS game music archive. `cmfinfo.py` prints one JSON summary per file,
used to check timing, channels and the CMF-specific controller events.

## Output

| field | meaning |
|---|---|
| `version` | `major.minor` from the header |
| `title`, `composer`, `remarks` | the header strings, `null` when not present |
| `instruments`, `tempo` | the instrument count and the basic tempo (`null` when the version has no tempo field) |
| `channels_used` | MIDI channels the channel table marks as in use |
| `ticks_per_quarter`, `ticks_per_second` | from the header |
| `ticks`, `duration` | the total of all delta times, and the playing time in seconds as a fraction string |
| `notes` | note-on events that start a note |
| `markers` | marker controller events |
| `rhythm_mode_on` | controller events that switch rhythm mode on |
| `transpose` | transpose controller values in order, upward positive and downward negative |
| `end_of_track` | `true` when an end-of-track meta event ends the music |

The header of each version, the event stream (delta times, running status,
meta and system exclusive events) and the CMF controllers follow the
Creative Music File format.

## Layout

- `cmfkit/cmf.py` – header, strings, events
- `cmfinfo.py` – command line

## Running

    python3 cmfinfo.py examples/dune.cmf examples/intro.cmf
    python3 -m unittest discover -s tests
