# Fragmented MP4 timeline rebuild

Run: `python3 frag.py input.json output.json`

Input: `tracks`: each `{"id","timescale","fragments":[...arrival order...]}`.
Each fragment `{"bmdt":int,"samples":[{"duration","size"}]}` — bmdt is the
base media decode time IN TIMESCALE UNITS of the fragment's first sample;
fragments arrive out of order and may overlap (an EDIT revising an
earlier range wins by later `seq` field value when `seq` present, else
arrival order). `targets`: `{"track","t_ms"}` playback-position probes.

Rules: per track sort samples by decode time (bmdt + cumulative duration
of preceding samples in the same fragment) — NOT by arrival order.
Overlapping samples (same decode time) from a higher-`seq` fragment
replace the lower one; identical bmdt fragments with no seq keep last
arrival. Build `timeline` = merged sorted sample list `{"pts","dur",
"size"}` in timescale units plus `gaps`: decode-time holes with no
sample (a gap needs its own entry `{"pts","dur"}` covering the hole;
holes at stream end are NOT gaps). For each target find the sample
active at t_ms — convert ms→timescale by `t*timescale/1000` truncating
to int; a probe inside a gap or past the last sample reports
`{"found":false,"nearest":pts of next sample or null}` while others
still resolve. Output `{"tracks":[{"id","timeline":[...],"gaps":[...]}],
"answers":[{"track","t_ms","found":bool,"pts","dur","size"}...]}` in
request order, plus `duration_ms` per track = (last pts+dur)/timescale.

## Binary fragmented MP4 indexer (`fmp4.py`)

`python3 fmp4.py FILE [OUT.json]` parses the `tfhd`/`tfdt`/`trun` subset
of an fMP4 file (`ftyp`, `moov`/`trak`/`tkhd`/`mdhd`/`hdlr`/`mvex`/`trex`,
`moof`/`mfhd`/`traf`, `mdat`) and emits, per track:

* every sample's source byte range (`offset`,`end`,`size`, sha256 digest),
  DTS, signed-composition-offset PTS, duration, flags and keyframe state;
* `decode_order` (DTS walk across all trun runs) and `timeline` (PTS sort,
  so B-frame style negative offsets display in presentation order);
* per-fragment continuation state (`cont`, `gap`, `expected_dts`) spanning
  fragments; `cont` is also stamped on each fragment's first sample.

Box reading is offset based and bounds checked (32-bit, 64-bit largesize
and size-0-to-EOF headers; unknown boxes skipped by declared size; no read
past a container end). Each trun resolves its own `data_offset` against the
tfhd base (`base-data-offset`, else moof origin), so a fragment's second
run is never located through the first run's offset. Per-sample fields
fall back to tfhd defaults and then trex defaults.

A truncated mdat leaves a locatable `truncated_mdat` error (track,
fragment sequence, trun, sample index, offset, missing bytes) while every
complete track and fragment stays indexed. Truncated/invalid box headers
produce `truncated_box` / `invalid_box_size` entries likewise.

`fmp4.ChunkedReader` limits each `read()`; the index only uses `seek()` /
short-read-tolerant reads, so the result is byte-identical for any chunk
size and repeat/seek playback never depends on buffered read history.

## Acceptance fixtures and tests

* `mkmp4.py` builds `out/full.fmp4` (interleaved video id 1 / audio id 2,
  multiple trun runs, signed negative composition offsets, tfhd and trex
  default sample fields, explicit vs. moof-anchored data offsets, a gap
  and seamless continuation) and `out/truncated.fmp4` (cut inside the last
  mdat).
* `python3 -m unittest test_fmp4 test_frag` runs all checks: byte spans and
  digests vs. the builder, DTS/PTS timelines, keyframe/default resolution,
  continuation, truncation recovery, chunk-size independence (1..4096),
  repeated seek positioning, largesize/size-0 headers, and the JSON
  timeline rules.

