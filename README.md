# De-esser band attenuation

Run: `python3 deess.py input.json output.json`

Input `{"frames":[{"t","low","high"}...],"threshold":T,"ratio":R,
"attack":A,"release":L}` — per-frame band energies (ms timeline).

Detector `det = high / max(low, 1e-9)`. Gain computer:
- `over = det - T` when det>T else 0; `want = min(1.0, 1.0/R)**...`
  simplified: `want = max(1.0/R, 1 - over*R)`.
- Smoothing: `gain = gain_prev + (want-gain_prev)/A` when
  want<gain_prev (attack, A in ms→ per frame step with frame dur
  10ms: use `1-exp(-10/A)`), else `gain = gain_prev +
  (want-gain_prev)*(1-exp(-10/L))`.
- Output per frame `{"t","det","gain"}` with det 3 decimals and
  gain 4 decimals; plus `{"active_frames":count where gain<0.99}`.
- If high==0 and low==0, det reports 0.
