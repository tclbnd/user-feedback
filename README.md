# FLAC fixed-predictor subframe reconstruction

Run: `python3 flac.py input.json output.json`
Tests: `python3 -m unittest test_flac -v`
Sample fixture: `python3 make_fixture.py` then
`python3 flac.py sample_fixture.json sample_output.json`

## Input contract

`{"frames":[{"index","channel_mode","subframes":[...]}...]}` —
each frame carries 1 (mono) or 2 (stereo) subframes; `channel_mode` is
`independent` | `left_side` | `right_side` | `mid_side`.

### Subframe types

* `{"type":"constant","value":v,"block":n}` — `n` repeats of integer `v`.
* `{"type":"verbatim","samples":[...]}` — samples copied verbatim; an
  optional `"block":n` must equal the sample count or the frame is corrupt.
* `{"type":"fixed","order":0..4,"warmup":[w0..],"residuals":[r...],
  "escape":[...]}` — residuals may contain `{"escape":v}` items meaning an
  escaped raw value `v` (negative allowed). This is the legacy flat form.
* The same fixed subframe may instead supply Rice partitions, either as
  metadata (`"partitions":[{"rice_param":k,"residuals":[...]}, ...]`,
  a partition may set `"escape":true` and list raw signed residuals) or as
  a bit-packed FLAC-style stream:

  ```json
  {"type":"fixed","order":2,"block":16,"warmup":[w0,w1],
   "bits":{"data":"<hex|base64|binary string>","length":350},
   "partition_order":2,"bps":16,"rice_parameter_bits":4}
  ```

  `length` is the number of significant bits (the last byte may be only
  partially used); `rice_parameter_bits` is 4 (default) or 5. A Rice
  parameter equal to the all-ones code marks an escape partition whose
  residuals are raw `bps`-wide two's-complement signed integers.

## Fixed predictor reconstruction

`block = len(warmup) + len(residuals)`:

- order 0: `s[i] = r[i]`
- order 1: `s[i] = r[i] + s[i-1]`
- order 2: `s[i] = r[i] + 2*s[i-1] - s[i-2]`
- order 3: `s[i] = r[i] + 3*s[i-1] - 3*s[i-2] + s[i-3]`
- order 4: `s[i] = r[i] + 4*s[i-1] - 6*s[i-2] + 4*s[i-3] - s[i-4]`

Warm-up occupies the first `order` samples verbatim. All arithmetic is
signed Python integer arithmetic, so a negative residual can never wrap
into a positive sample. Residuals are zig-zag mapped
(`u = (r << 1) ^ (r >> 63)` style) before Rice coding and mapped back with
the inverse sign extension when decoding.

Partitions tile the block: each covers `block / 2^partition_order` sample
slots; the first partition loses the `order` warm-up slots. Every
partition carries its own Rice parameter, so a parameter change at a
partition boundary never leaks into the next partition (no boundary
spike).

## Stereo restore

`channel_mode` applies to channel pair `[a,b]`:

- `left_side`:  `L=a, R = a - b`
- `right_side`: `R=b, L = a + b`
- `mid_side`:   side=b, mid=a; `L = mid + ((side+1)>>1)`,
  `R = mid - (side>>1)` — arithmetic shifts; `side` may be negative.
- `independent`: pass through.

## Errors and frame boundaries

A subframe whose declared `"block":n` is larger than its decodable length
is corrupt. The frame emits an error object and decoding continues with
the next frame — a bad frame never consumes the following complete frame:

```json
{"index":5,"error":"truncated","partition":2,"bit":140,"detail":"..."}
```

* `error` is `truncated` (declared range not fully supplied / bitstream
  ended early), `length` (more data than declared) or `malformed`
  (structurally invalid subframe/frame).
* `partition` is present when the failure is localised to a Rice
  partition (0-based), and `bit` is the bit cursor position at failure for
  bit-packed subframes. Successful bit-packed frames report
  `bits_consumed` and `partitions` per subframe.

## Output

`{"frames":[{"index","channels":[[...],[...]]? | error-object}...]}`.
Mono frames carry one channel array, stereo frames two.

