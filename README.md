# discidkit

Lookup helper for the CD ripping station. `discid.py` reads a disc's table of
contents (one text file per disc, written by the drive tool) and prints the
identifiers used to look the disc up in FreeDB, MusicBrainz and AccurateRip.

## Input

    # comment
    TRACK 01 AUDIO 0
    TRACK 02 AUDIO 18321
    TRACK 03 DATA 260000
    LEADOUT 290000

Addresses are sector numbers (75 per second) as the drive reports them,
without the 150-sector lead-in that the disc ID algorithms count in.

## Output

| field | meaning |
|---|---|
| `tracks`, `audio_tracks`, `data_tracks` | track counts (integers) |
| `audio_seconds` | length of the audio session in seconds as a fraction string |
| `cddb_id` | the FreeDB/CDDB disc ID, 8 lower-case hex digits |
| `musicbrainz_id` | the MusicBrainz disc ID (27 characters, URL-safe base64 with no padding) |
| `accuraterip_id` | `NNN-xxxxxxxx-xxxxxxxx-xxxxxxxx`: audio track count, the two AccurateRip disc IDs and the CDDB disc ID, lower-case hex |

The three identifiers follow the FreeDB, MusicBrainz and AccurateRip disc ID
algorithms. On an Enhanced CD (audio tracks followed by a data track in a
second session) the MusicBrainz and AccurateRip IDs describe the audio
session only; the CDDB ID counts every track.

## Layout

- `discidkit/discid.py` – TOC reader and the three algorithms
- `discid.py` – command line

## Running

    python3 discid.py examples/album.toc examples/enhanced.toc
    python3 -m unittest discover -s tests
