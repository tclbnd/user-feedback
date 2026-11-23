# sndkit

Check for the sound-effects archive kept as Sun/NeXT audio files (`.au`,
`.snd`), recorded over the years on workstations, voice-mail systems and
streaming servers. `sndinfo.py` reads each file and prints one JSON summary,
used to catalogue the archive and to find clipped or silent recordings.

## Output

| field | meaning |
|---|---|
| `encoding` | `mulaw`, `alaw`, `pcm8`, `pcm16`, `pcm24`, `pcm32`, `float32`, `float64`, `g721`, `g722`, `g723_3`, `g723_5` |
| `bits` | bits per sample of the encoding |
| `sample_rate`, `channels` | from the header |
| `frames`, `duration` | whole sample frames in the audio data; seconds as a fraction string |
| `annotation` | the annotation text between the header and the audio data up to the first NUL, decoded as ISO 8859-1, `null` when empty |
| `size_known` | the header gives the data size (streamed files leave it unknown) |
| `truncated` | the file holds less audio than the header's data size |
| `peak` | largest sample magnitude as a fraction of full scale, `%.6f` (mu-law and A-law decoded to 16-bit linear; `null` for the ADPCM encodings) |
| `channel_peaks` | the same peak for each channel of the interleaved data, `null` for the ADPCM encodings |
| `clipped` | the number of samples at full scale: the most negative or most positive integer of the sample size for linear PCM, the largest-magnitude code (±32124 mu-law, ±32256 A-law) for G.711, magnitude 1.0 or more for floating point; `null` for the ADPCM encodings |

All header fields and samples are big-endian. Audio data starts at the data
offset given in the header.

## Layout

- `sndkit/snd.py` – header, encodings, sample decoding
- `sndinfo.py` – command line

## Running

    python3 sndinfo.py examples/doorbell.au examples/voicemail.au
    python3 -m unittest discover -s tests
