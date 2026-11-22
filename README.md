# bnkkit

Build check for the game's Audiokinetic Wwise SoundBanks (`.bnk`).
`bnkinfo.py` reads each bank and prints one JSON summary, used to catch banks
that reference audio they do not carry before the build ships.

## Output

| field | meaning |
|---|---|
| `version`, `bank_id` | from the BKHD chunk |
| `bank_name` | the bank's own name from the STID string table, else `null` |
| `chunks` | chunk tags in file order |
| `media` | embedded media from DIDX: `id`, `offset` (from the start of the DATA payload), `size` |
| `data_bytes` | size of the DATA payload |
| `media_outside_data` | some DIDX entry points outside the DATA payload |
| `objects` | HIRC object counts by type: `state`, `sound`, `action`, `event`, `random_sequence`, `switch`, `actor_mixer`, `bus`, `blend`, `music_segment`, `music_track`, `music_switch`, `music_playlist`, `attenuation`, else `type_N` |
| `sounds` | sound objects by source type: `embedded`, `prefetch`, `streamed` |
| `events` | per event: `id` and the ids of its `actions` |
| `missing_media` | sorted ids of media that embedded or prefetched sounds use but the bank's DIDX does not list |

Field layouts follow the bank version in BKHD.

## Layout

- `bnkkit/bnk.py` – chunks, media index, HIRC
- `bnkinfo.py` – command line

## Running

    python3 bnkinfo.py examples/Music.bnk examples/UI_Legacy.bnk
    python3 -m unittest discover -s tests
