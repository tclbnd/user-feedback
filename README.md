# rmkit

Catalogue checker for the RealMedia (`.rm`, `.ra`, `.rmvb`) part of the web
archive. `rminfo.py` reads each file's chunks and prints one JSON summary.

## Output

| field | meaning |
|---|---|
| `duration` | from the PROP chunk, in seconds as a fraction string |
| `preroll`, `avg_bitrate` | from the PROP chunk (milliseconds, bits per second) |
| `streams` | media streams in the file (not the logical file-info entry), see below |
| `title`, `author`, `copyright`, `comment` | CONT chunk |
| `indexed` | the file has an INDX chunk |
| `data_chunks` | DATA chunks in the file |

Each stream has `number`, `mime`, `name`, `type` (`audio`, `video`, `other`),
`rates` (encodings in a multi-rate stream, 1 otherwise), `packets` and
`keyframes` (packets flagged as keyframes in all DATA chunks). Audio adds
`codec` (FourCC such as `cook`, `sipr`, `28_8`), `ra_version`, `sample_rate`,
`bits`, `channels`; video adds `codec` (`RV30`, `RV40`, ...), `width`,
`height` and `fps` (frames per second as a fraction string). For a multi-rate
stream the fields describe its first encoding.

## Layout

- `rmkit/rm.py` – chunks, stream headers, packets
- `rminfo.py` – command line

## Running

    python3 rminfo.py examples/concert_1999.rm examples/radio_talk.ra
    python3 -m unittest discover -s tests
