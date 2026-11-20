# flacmeta

Labels deliver lossless masters as FLAC. Ingest reads the metadata blocks
(stream info, Vorbis comments, seek table, cover art) to build the release
sheet, check required tags and decide whether a file needs re-tagging.

Metadata is parsed per RFC 9639. Parsing is strict: any truncated or malformed
block raises `flacmeta.blocks.FlacError` instead of guessing, so audio frames
are never consumed as metadata. A leading ID3v2 tag (allowed by the spec) is
skipped before the `fLaC` marker.

## Output conventions

- `tags` maps each **upper-cased** field name to a list of values in the order
  the fields appear, so repeated fields such as two `ARTIST` comments are all
  retained.
- `total_samples` is `null` when the encoder recorded "unknown" (the all-ones
  36-bit value); `duration` is then `null` as well rather than a fabricated
  number. Otherwise `duration` is the exact length in seconds as a fraction.
- `pictures[].mime` and the other PICTURE fields come from big-endian integers
  with bounds-checked lengths; no bytes from later fields leak into the MIME.
- `audio_offset` is the first byte after the last metadata block (i.e. the
  start of the audio frames), accounting for any skipped ID3v2 tag.

## Layout

- `flacmeta/blocks.py` – ID3v2 skip and metadata block walking
- `flacmeta/parse.py` – STREAMINFO, VORBIS_COMMENT, SEEKTABLE, PICTURE
- `flacmeta/probe.py` – catalogue summary
- `flacmeta/catalog.py` – release sheet checks
- `flacinfo.py` – command line

## Running

    python3 flacinfo.py examples/*.flac
    python3 -m unittest discover -s tests
