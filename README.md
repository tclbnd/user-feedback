# Event recording

This recorder packages independently captured H.264 video and AAC audio
single-track fragments into a two-track HLS EVENT playback.  It supports
out-of-order and repeated delivery, truncated-then-retransmitted fragments,
permanent holes, recording restarts and initialisation changes, and it
publishes the playlist atomically so that a process kill at any point leaves a
readable state.

Python 3.9+ is required.  FFmpeg (with libx264 and AAC) is only used by the
synthetic fixture generator and by the acceptance suite's final decode check;
publication itself is a pure-Python container rewrite and never re-encodes
media.

```sh
python3 fixtures.py sample
python3 recorder.py sample/capture.jsonl published
python3 -m unittest discover -s tests -v
python3 verify.py                 # full local acceptance (needs FFmpeg)
```

Set `FFMPEG` to an executable path if it is not on `PATH`.

## Capture log

The log is JSON Lines.  All relative media paths are resolved against the
directory containing the log.

- `open` starts a recording generation.  `epoch` is its identifier and each
  entry of `tracks` gives the track's single-track `init` segment, media
  `timescale` and `clock_origin` in track ticks.  The two origins identify the
  same recording instant and are how video and audio are aligned.  Several
  `open`/`seal` pairs may appear in one log; they are published in order.
- `fragment` delivers `(epoch, track, seq)`.  `path` points at a file holding
  one moof and mdat; `sha256` is the content digest.  `size`, when present,
  must also match.  Arrivals may be out of order and a slot may be delivered
  more than once.  A delivery that is missing, truncated or fails its digest is
  ignored and may be followed by a complete one.  Two *complete* deliveries for
  the same slot with different content are a conflict and abort publication.
- `seal` gives the final expected sequence number per track in `last`.  Holes
  are reported as permanent only for a sealed generation.

`read_capture` returns a session with an ordered `epochs` list.  Each track
contains its accepted `fragments`, the `missing` sequence numbers (only for
sealed generations) and rejected attempts.  Arrival order and duplicate
delivery therefore cannot influence publication.

## Packaging

`mux.py` parses the source fragments and writes, without decoding:

- one multi-track `init.mp4` (a fresh movie header plus the original `trak`
  boxes, so codec configuration is untouched; track IDs are 1 and 2),
- one `segment-NNN.m4s` per closed video GOP.  Every segment starts with the
  independently decodable keyframe, keeps the original decode order and
  PTS/DTS composition offsets (including B frames) and copies the compressed
  sample bytes verbatim,
- an HLS EVENT playlist (`index.m3u8`) with `EXT-X-MAP`, `EXT-X-ENDLIST` and
  `EXT-X-INDEPENDENT-SEGMENTS`.

Audio is assigned to video GOPs by presentation time after both tracks have
been normalised with their `clock_origin`, so a 29.97 fps video and 48 kHz AAC
stay aligned.  With a permanent hole, samples before the next keyframe are
discarded, audio covering the hole is not published, and the next independently
decodable GOP resumes.  A new generation whose zeroed timestamps would overlap
the previous one is rebased forward and marked with `EXT-X-DISCONTINUITY`; a
changed initialisation produces a new init file and a new `EXT-X-MAP` at that
discontinuity.

Segmentation is GOP based and uses no future data, so duplicate delivery,
reordering and a later replay all produce identical segment bytes.

## Publication and crash safety

`playlist.py` publishes in this order:

1. each media file is written to a temporary name, fsynced and atomically
   renamed; unchanged files are left untouched,
2. the playlist is atomically replaced,
3. `progress.json` is atomically written.

At every instant a reader sees either the old or the new complete playlist,
and every playlist only references files that are already readable.  A replay
of the same log reproduces the same playlist and identical segment bytes.

The acceptance suite can force a kill at the two interesting points by setting
`RECORDER_CRASH_AT` (or `HLS_CRASH_AT`):

- `before_playlist` (aliases `after_segments`, `after_fragments`,
  `after_fragment_write`, `segments_written`): after all media writes, before
  the playlist replacement,
- `after_playlist` (aliases `before_progress`, `playlist_written`): after the
  playlist replacement, before the progress record.

The process kills itself with `SIGKILL` at that point.

## Acceptance

`verify.py` builds synthetic fixtures (normal, reordered/repeated delivery,
truncated-then-complete delivery, permanent hole, timestamp reset and
initialisation change), publishes them, checks every retained compressed
packet against the capture (payload digest, decode/presentation timestamps and
A/V offset, per recording generation), decodes both tracks with FFmpeg, and
kills/replays the two crash points.  It uses only local files and the local
FFmpeg build.
