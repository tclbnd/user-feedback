# Echo-canceller double-talk freezer

Two layers share one rule set: a frame-energy control plane (`aec.py`,
JSON in/out) and a sample-level adaptive canceller (`aec_core.py`).

## 1. Frame-energy control — `python3 aec.py input.json output.json`

Input `{"frames":[{"t","farend","nearend","mic"}...],"win":W}` —
per-frame signal energies; `win` ms look-back for estimates.

Per frame compute `est = echo estimate = 0.7 * max(farend) over the
last W ms (inclusive)` and `ser = mic / max(est, 1e-9)`. Rules:
- `ser >= 0.5`, or a zero (silent) reference -> freeze adaptation
  (`state:"frozen"`) and HOLD the previous `used_est`; the current
  frame's `est` is never published while frozen.
- A double-talk freeze persists while `nearend > 0.3 * est_prev`.
  When the near-end drops to/below that level the frame is still served
  frozen and adaptation resumes on the NEXT frame (hysteresis / agreed
  resume state).
- A reference-silence freeze ends immediately on the first active
  reference frame.
- Delay change: while adapting, if `est` differs from the previous
  `used_est` by more than 40%, emit `{"t","kind":"delay-shift"}` in
  `alerts` and resync `used_est` immediately (state stays adapting).
  A cold start (`used_est == 0`) never alerts.
- Output `{"frames":[{"t","state","used_est","ser"}...],
  "alerts":[...]}`; `ser` rounded to 3 decimals, `used_est` to 2.

## 2. Sample-level canceller — `aec_core.AEC`

Streaming, pure standard library. Internally it processes sample by
sample inside fixed `frame_len` (8 ms) control frames, so the caller's
block size cannot change any state transition.

- **Adaptive filter:** FIR of `FILTER_TAPS` coefficients on a bulk-delay
  reference line, updated sample-by-sample with normalised LMS while
  adapting. The reference window is indexed so tap `k` sees
  `x(n-(delay+k))`, matching the microphone convolution.
- **Double-talk detector:** Geigel-style test on EMA-smoothed mic/ref
  RMS with an entry threshold, an exit threshold, consecutive-frame entry
  confirmation, a minimum hold, and a multi-frame exit hysteresis. It is
  gated by a warm-up window and a latched convergence flag so an
  un-converged filter cannot freeze itself, and so the detector stays
  armed while the double-talk residual is high.
- **Freeze rules:** double-talk OR reference silence freezes both the
  coefficients and the delay estimate; the last coefficients keep
  producing the echo estimate so near-end voice amplitude is preserved.
  Double-talk resumes on the next frame after the release condition;
  silence resumes on the first active frame.
- **Delay relocation:** normalised cross-correlation over a sliding
  window, evaluated only while adapting. The correlation lag is bulk
  delay plus the echo-path peak tap; the peak offset is latched once
  from the converged initial filter, so relocations recover the true
  bulk delay. Small lag jitter is tracked silently; a move beyond
  tolerance emits a `delay-shift` event, softly shrinks the
  coefficients and briefly boosts the NLMS step for fast reconvergence.
- **Flushing / diagnostics:** `flush()` zero-pads and drains a partial
  frame while holding the previous state (no spurious freeze/resume),
  and returns the final state. Every frame emits a diagnostic record
  (state, reason, delay, powers, ERLE marker, coefficient norm) and the
  full event log is returned at the end.

## 3. Fixture and acceptance

- `fixture.build_fixture()` deterministically generates the reference
  playback signal, a fixed normalised FIR echo path, a piecewise
  bounded bulk-delay schedule, gated near-end voice, a reference silence
  gap, and the mixed microphone signal (seeded LCG, no external files).
- `acceptance.py` measures the four criteria separately and exits
  non-zero on any failure:
  1. single-talk residual echo (ERLE, adapting/non-double-talk samples),
  2. double-talk near-end voice preservation (least-squares voice gain on
     frozen frames) plus freeze coverage,
  3. variable-delay recovery (relocation magnitude vs schedule, latency,
     post-shift ERLE),
  4. block boundaries — identical freeze/resume sample times, state
     traces and output samples across several block sizes, plus a
     partial-frame `flush()` check.
  Reference-silence freezing is checked separately.
- `test_aec_control.py` are unit tests for the JSON control contract.

Run:
```
python3 test_aec_control.py
python3 acceptance.py
```

