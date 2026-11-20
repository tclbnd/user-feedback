# telcodec

The call-recording archive stores trunk recordings as raw G.711 (μ-law from
the North American carriers, A-law from the European ones). `telcodec`
converts between the 8-bit codes and 16-bit linear PCM for transcription,
redaction and re-export, and marks speech activity for the review player. Newer switches deliver
recordings as WAV files (G.711 or 16-bit PCM, mono or two-channel for the
two call directions), and exports must open in the standard players.

## Layout

- `telcodec/g711.py` – μ-law and A-law encode/decode
- `telcodec/vad.py` – voice activity
- `telcodec/wav.py` – WAV files with G.711 or PCM audio
- `g711calc.py` – single values and raw file conversion
- `g711wav.py` – WAV inspection and conversion

## Running

    python3 g711calc.py examples/values.json out.json
    python3 g711calc.py --decode ulaw examples/carrier_a.ul out.pcm
    python3 g711wav.py info examples/switch_b_stereo.wav
    python3 g711wav.py decode examples/switch_a_mono.wav out_pcm.wav
    python3 g711wav.py encode alaw out_pcm.wav out_alaw.wav
    python3 -m unittest discover -s tests
