# oggscan

Voice notes in the messaging app are Ogg Opus files recorded on phones and
web clients. The service shows each note's length in the chat bubble, bills
transcription by the second and drops files that fail integrity checks.

## Layout

- `oggscan/ogg.py` – pages and packets
- `oggscan/opus.py` – OpusHead, OpusTags and packet TOC
- `oggscan/probe.py` – duration and stream figures
- `oggscan/retention.py` – inbox retention
- `opusinfo.py` – command line

## What the figures mean

The parser follows RFC 3533 (Ogg) and RFC 7845 (Ogg Opus):

- A page's lacing table is honoured: a 255 is a continuation, a smaller
  value ends a packet, and a packet may span pages.
- Opus granule positions count samples on the fixed 48 kHz Opus clock. The
  `input sample rate` in OpusHead (e.g. 16000 for a phone recording) is
  informational and is **not** used to convert time.
- `playable_samples = granule_end - pre_skip`. The final EOS granule may be
  smaller than the page's natural end, which trims the last packet.
- Packet durations come from the TOC; code 3 uses its explicit frame count.
- Skeleton streams (`fishead`/`fisbone`) are identified and excluded, so
  their millisecond granules never become the audio end.
- Multiplexed streams are separated by serial number; chained files (a new
  BOS after data) each contribute their own playable length.
- `duration` is the exact rational number of seconds as a string,
  `duration_seconds` is the same value as a float.
- Damage is counted, not hidden: `crc_errors` (bad page checksums),
  `sync_errors` (junk skipped while resyncing), `sequence_errors`,
  `continuity_errors` (a packet continuing with no start, or left open),
  `packet_errors` (unparseable Opus packets) and `truncated`.

## Running

    python3 opusinfo.py examples/*.opus
    python3 -m unittest discover -s tests
