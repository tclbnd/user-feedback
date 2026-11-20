# flacmeta

Labels deliver lossless masters as FLAC. Ingest reads the metadata blocks
(stream info, Vorbis comments, seek table, cover art) to build the release
sheet, check required tags and decide whether a file needs re-tagging.

Decoding follows RFC 9639. A leading ID3v2 tag is skipped when present;
everything else before the `fLaC` marker is rejected. Malformed or truncated
metadata raises `flacmeta.blocks.FlacError` instead of being guessed at, so
audio frames are never parsed as metadata.

## Conventions

- `tags` maps an upper-case field name to a **list** of values; repeated
  fields (e.g. two `ARTIST` comments) keep every value in order of appearance.
- STREAMINFO channels and bits-per-sample are stored minus one and decoded
  with the required `+1`; total samples is a full 36-bit value.
- VORBIS_COMMENT integers are little endian; STREAMINFO/PICTURE integers are
  big endian.
- `duration` is `null` when the total sample count is unknown (zero); no
  duration is ever invented.

## Layout

- `flacmeta/blocks.py` – metadata block walking (ID3v2 skip, chain checks)
- `flacmeta/parse.py` – STREAMINFO, VORBIS_COMMENT, SEEKTABLE, PICTURE, PADDING
- `flacmeta/probe.py` – catalogue summary
- `flacmeta/catalog.py` – release sheet checks
- `flacinfo.py` – command line

## Running

    python3 flacinfo.py examples/*.flac
    python3 -m unittest discover -s tests
