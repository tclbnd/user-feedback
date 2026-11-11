# Event recording

The recorder packages independently received single-track H.264 (video) and
AAC (audio) fragmented-MP4 captures into a dual-track HLS **EVENT** playlist.
It runs on Python 3.9+ and an FFmpeg build with libx264 and AAC for the
synthetic fixtures. Set `FFMPEG` to an executable path if ffmpeg is not on
`PATH` (the repository bundles one under `bin/`).

## One-click acceptance

```sh
sh accept.sh
```

This synthesizes all fixtures locally and runs the full unittest suite, which
actually decodes every published dual-track output and, per recording
generation, verifies retained packet payload digests, the PTS/DTS relationship
and audio/video alignment. It also forces process kills at both crash points
and replays the same log to confirm the result is byte-identical to an
uninterrupted baseline.

The original entry points still work:

```sh
python3 fixtures.py sample sample/
python3 recorder.py sample/capture.jsonl published/
python3 -m unittest discover -s tests -v
```

## Capture log (JSONL)

All relative media paths resolve against the directory containing the log.

- `open` defines an `epoch` (recording generation) and each track's
  initialization path, `timescale` and `clock_origin` in track ticks. The two
  origins map to the same recording instant. Multiple `open` events describe
  successive generations (e.g. a capture restart that zeroes timestamps).
- `fragment` names the `epoch`, `track` (`audio`/`video`), one-based `seq`,
  media `path`, `sha256` and a content `summary`. Each file holds one
  `moof`+`mdat`. A fragment may arrive while up to four earlier sequence
  numbers of the same track are still pending, and may be redelivered.
- `seal` gives each track's final expected sequence number in `last`.

### Delivery semantics

- **Out of order / redelivery** are handled within a four-fragment reorder
  window. Reordering and duplicate delivery of identical, verified bytes
  produce the same publication as in-order arrival.
- **Conflict**: a complete, checksum-verified fragment with the same
  `(epoch, track, seq)` but different content raises `ingest.ConflictError`.
- **Truncation**: a delivery whose checksum does not match stays incomplete and
  may be replaced by a later complete redelivery of the same sequence. An
  incomplete fragment still present at seal time is an error.
- **Permanent loss** is only knowable after `seal`: missing numbers are then
  reported per track in `progress.json`.

## Publication and recovery

- Output is dual-track fMP4 (`init-NN.mp4` + `segment-NNN.m4s`) plus a playable
  `index.m3u8` (`#EXT-X-PLAYLIST-TYPE:EVENT`, terminated by `#EXT-X-ENDLIST`).
- Segments begin on a closed-GOP, independently decodable video boundary.
  B-frame decode order and the PTS/DTS composition offset are preserved, and
  the non-integer-rate video (30000/1001 in 90 kHz ticks) is aligned to the
  48 kHz audio through the two clock origins. Retained compressed sample
  payloads are copied byte-for-byte (never re-encoded).
- On a permanent video gap, publishing resumes at the next independently
  decodable video boundary; audio belonging to the dropped interval is
  discarded and the retained audio is re-aligned. The boundary is tagged with
  `#EXT-X-DISCONTINUITY`.
- A capture restart that zeroes timestamps starts a new generation placed
  contiguously on the output timeline and tagged as a discontinuity. When the
  codec configuration changes, a new initialization section is published and
  selected with a new `#EXT-X-MAP` tag.

### Crash safety

Writes are ordered and atomic (temp file + `rename`):

1. initialization sections, then each segment;
2. the playlist is replaced only once every file it references is on disk;
3. `progress.json` is written last.

At every instant a readable playlist references only readable files. Two
environment variables force an immediate process exit for recovery testing:

- `RECORDER_CRASH_AFTER_SEGMENT` — after a segment write, before the playlist;
- `RECORDER_CRASH_BEFORE_PROGRESS` — after the playlist, before progress.

Replaying the same log after either crash reproduces the uninterrupted
baseline byte-for-byte, and already published entries stay stable.

## Modules

- `fmp4.py` — pure-Python fMP4 parsing/writing (payloads stay opaque).
- `ingest.py` — log ingestion, ordering window, conflict/truncation/seal rules.
- `mux.py` — deterministic dual-track packaging plan and crash-safe publish.
- `playlist.py` / `state.py` — atomic publication and the progress record.
- `verify.py` — per-generation, per-packet payload/timestamp/A-V verification.
- `fixtures.py` — synthetic scenarios: `sample`, `reorder`, `truncated`,
  `missing`, `reset`, `conflict`.
