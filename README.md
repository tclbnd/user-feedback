# asfkit

Catalogue checker for the Windows Media part of the broadcast archive.
`asfinfo.py` reads the header of ASF files (`.wma`, `.wmv`, `.asf`) and prints
one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `duration` | play duration minus preroll, in seconds as a fraction string; `null` for a broadcast (live) file |
| `preroll` | milliseconds, from the File Properties object |
| `packets` | data packets (`null` for a broadcast file) |
| `packet_size` | data packet size when minimum and maximum agree, else `null` |
| `broadcast`, `seekable`, `max_bitrate` | from the File Properties object |
| `streams` | every stream the file declares, by stream number: `number`, `encrypted`, `type` (`audio`, `video`, `other`); audio adds `codec` (format tag as `0x....`), `channels`, `sample_rate`, `bits`; video adds `codec` (FourCC), `width`, `height`, `frame_rate` (frames per second as a fraction string, `null` when the file does not give one) |
| `title`, `author`, `copyright`, `description` | Content Description object |
| `tags` | all attributes in the header by name, with typed values: strings, `true`/`false`, integers, byte arrays as hex, GUIDs as text; a name that occurs more than once gives a list of its values in file order |

## Layout

- `asfkit/asf.py` – header objects
- `asfinfo.py` – command line

## Running

    python3 asfinfo.py examples/concert.wma examples/news.wmv
    python3 -m unittest discover -s tests
