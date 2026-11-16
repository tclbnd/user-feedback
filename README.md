# FLAC fixed-predictor subframe reconstruction

Run: `python3 flac.py input.json output.json`

Input `{"frames":[{"index","channel_mode","subframes":[...]}...]}` —
each frame carries 1 or 2 subframes; channel_mode is
`independent` | `left_side` | `right_side` | `mid_side`.
A subframe is `{"type":"constant","value":v,"block":n}` |
`{"type":"verbatim","samples":[...]}` |
`{"type":"fixed","order":0..4,"warmup":[w0..],"residuals":[r...],
"escape":[...]}` — residuals may contain `{"escape":v}` items
meaning an escaped raw value v (negative allowed).

Fixed predictor reconstruction (block = len(warmup)+len(residuals)):
- order 0: s[i] = r[i]
- order 1: s[i] = r[i] + s[i-1]
- order 2: s[i] = r[i] + 2*s[i-1] - s[i-2]
- order 3: s[i] = r[i] + 3*s[i-1] - 3*s[i-2] + s[i-3]
- order 4: s[i] = r[i] + 4*s[i-1] - 6*s[i-2] + 4*s[i-3] - s[i-4]
warmup occupies the first `order` samples verbatim.

Stereo restore (channel_mode applies to channel pair [a,b]):
- left_side:  L=a, R = a - b
- right_side: R=b, L = a + b
- mid_side:   side=b, mid=a; L = mid + ((side+1)>>1),
              R = mid - (side>>1)   — arithmetic shifts, side
              may be negative.
- independent: pass through.

A subframe with `"block":n` declared larger than its decodable
length is corrupt: emit `{"index","error":"truncated"}` for that
frame only and continue with the next frame.
Output `{"frames":[{"index","channels":[[...],[...]]?|error}...]}`.
