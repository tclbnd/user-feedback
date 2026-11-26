# swavkit

Check for the SWAV sounds (`.swav`) the audio team extracts from Nintendo DS
sound archives. `swavinfo.py` prints one JSON summary per file, so a
re-recorded effect can be compared with what the DS sound hardware plays.

## Input

SWAV files exactly as the DS SDK writes them: the `SWAV` file header and the
`DATA` block with the wave information (wave type, loop flag, sample rate,
channel timer, loop offset, non-loop length) followed by the wave data in
8-bit PCM, 16-bit PCM or IMA-ADPCM.

## Output

| field | meaning |
|---|---|
| `type` | `"pcm8"`, `"pcm16"` or `"adpcm"` |
| `looped`, `sample_rate`, `timer` | from the wave information |
| `timer_rate` | playback rate the channel timer gives, rounded to a whole Hz (DS sound clock 16756991 Hz) |
| `samples` | samples in the wave (loop part included) |
| `loop_start` | sample index the loop restarts at, `null` when not looped |
| `duration` | `samples / sample_rate` as an exact fraction |
| `complete` | `true` when all the wave data is in the file |
| `peak` | largest absolute decoded sample (16-bit scale) / 32768, six decimals |
| `last_sample` | last decoded sample on the 16-bit scale |

8-bit samples are scaled to 16 bits as `v × 256`.

## Layout

- `swavkit/swav.py` – headers, wave information, decoding
- `swavinfo.py` – command line

## Running

    python3 swavinfo.py examples/jump.swav examples/bgm_loop.swav
    python3 -m unittest discover -s tests
