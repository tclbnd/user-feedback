# filmkit

Check for the Sega FILM / CPK movies (`.cpk`, also `.film`) from Saturn and
Sega CD era discs that the team re-masters. `filminfo.py` reads each file's
stream descriptions and sample table, follows every packet the way the
player does, decodes the uncompressed (PCM) audio, and prints one JSON
summary per file. Video packets are not decoded.

## File layout

The file exactly as shipped (FILM header, FDSC stream description, STAB
sample table, then the packet data).

## Output

| field | meaning |
|---|---|
| `version`, `base_clock` | header version and the video time base (ticks per second) |
| `packets` | one object per sample table entry, in table order: `stream` (`"audio"` or `"video"`), `pts`, `duration` (to the next packet of the same stream, `null` for the last one), `key`, `size` and `ok` (`false` when the packet's bytes are not all in the file) |
| `video` | `null` when the player has no video stream, otherwise `codec` (`"cinepak"` or `"raw"`), `width`, `height`, `frames`, `keyframes` |
| `audio` | `null` when the player has no audio stream, otherwise `codec` (`"pcm_s8_planar"`, `"pcm_s16be_planar"` or `"adx"`), `rate`, `channels`, `bits`, `samples` (per channel, from the sample table), and for PCM `peak` and `crc32` of the decoded audio as interleaved signed 16-bit little-endian samples (8-bit audio widened by 256), lower-case hex |

Audio `pts` and `samples` count samples per channel; video `pts` is in
`base_clock` ticks.

## Layout

- `filmkit/film.py` – header, sample table and audio reader
- `filminfo.py` – command line

## Running

    python3 filminfo.py examples/opening.cpk examples/lemmings.film
    python3 -m unittest discover -s tests
