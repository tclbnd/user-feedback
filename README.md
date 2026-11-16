# RF64 sample reader

Run: `python3 rf64.py input.json output.json`

Input: `channels`, `sample_rate`, `bytes_per_sample`, `riff_size`
(32-bit RIFF declared size, may be the RF64 sentinel 0xFFFFFFFF),
`ds64_size` (real data size in FRAMES when riff_size is the sentinel,
else ignored), `interleaved` (flat int array, frame-major L0,R0,L1,R1…),
`requests` — `{"id","channel" or "all","start_frame","count"}`.

Rules: true frame count = ds64_size when riff_size==0xFFFFFFFF else
riff_size. start_frame must satisfy 0 <= start < frames; a request
start out of range yields `{"id","error":"eof"}` while other requests
still run. count reads up to end-of-file (clip, never pad). channel
selects one channel (de-interleave by stride); "all" returns the raw
interleaved span. Output `{"results":[{"id","channel","start_frame",
"samples":[...]}|{"id","error":"eof"}], "frames_total":N}` preserving
request order. samples are raw ints; no endianness/scale conversion.

Notes:
- `riff_size == 0xFFFFFFFF` is a placeholder; only then does `ds64_size`
  carry the length. A real `riff_size` wins even if `ds64_size` is present.
- Unknown extra chunks (e.g. `JUNK`, `bext`, `LIST`) are ignored: they never
  shift the frame/channel stride, and an odd-sized chunk's pad byte is not
  part of the data chunk.
- `bytes_per_sample` is the container width and `bits_per_sample` the valid
  width; stored values are already container-aligned, so nothing is shifted.
- Reads stop at the declared end of file and at the last whole frame present
  in `interleaved`; a truncated file yields shorter sample lists (never
  padded), and `frames_total` still reports the declared length.

Tests: `python3 -m unittest -v test_rf64`

