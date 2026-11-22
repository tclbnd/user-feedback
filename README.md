# hlskit

Checker for the catch-up TV packager. `hlsinfo.py` reads HLS playlists
(`.m3u8`) and prints one JSON summary per playlist, so operations can see what
the players will fetch.

## Output

A master playlist gives `type: "master"`, `variants` (`bandwidth`, `codecs`,
`resolution`, `uri`) and `renditions` (number of `EXT-X-MEDIA` entries).

A media playlist gives `type: "media"`, `version`, `target_duration`,
`media_sequence`, `discontinuity_sequence`, `endlist`, `playlist_type`, and
`segments` in order, each with:

| field | meaning |
|---|---|
| `sequence` | media sequence number of the segment (segments skipped by a Playlist Delta Update still count) |
| `uri` | resolved against `--base` when given |
| `duration` | `EXTINF` duration in seconds as an exact fraction string |
| `byterange` | `[offset, length]`, or `null` for a whole resource |
| `map` | the Media Initialization Section that applies (`uri`, `byterange`), or `null` |
| `key` | encryption method that applies, or `null` |
| `iv` | for `AES-128`, the initialization vector as `0x` and 32 hex digits — the `IV` attribute, or the one RFC 8216 derives when it is absent; otherwise `null` |
| `discontinuity`, `gap`, `program_date_time` | from the tags of that segment |

plus `duration` (all segments), `playable_duration` (segments not marked as a
gap), `discontinuities` and `gaps`. Durations are exact: `6.006` is `3003/500`.

## Layout

- `hlskit/hls.py` – tags, attribute lists, segments
- `hlsinfo.py` – command line

## Running

    python3 hlsinfo.py --base https://cdn.example/show/ examples/master.m3u8 examples/episode.m3u8
    python3 -m unittest discover -s tests
