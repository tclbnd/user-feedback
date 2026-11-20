# flacmeta

Labels deliver lossless masters as FLAC. Ingest reads the metadata blocks
(stream info, Vorbis comments, seek table, cover art) to build the release
sheet, check required tags and decide whether a file needs re-tagging.

## Layout

- `flacmeta/blocks.py` – metadata block walking
- `flacmeta/parse.py` – STREAMINFO, VORBIS_COMMENT, SEEKTABLE, PICTURE
- `flacmeta/probe.py` – catalogue summary
- `flacmeta/catalog.py` – release sheet checks
- `flacinfo.py` – command line

## Running

    python3 flacinfo.py examples/*.flac
    python3 -m unittest discover -s tests
