# Pitch-shift resynthesis from partials

Run: `python3 pitch.py input.json output.json`

Input `{"partials":[{"freq","amp","phase"}...],"ratio":R,"sr":SR,
"frames":N}` — tracked sine partials of one note; resynthesize at
pitch ratio R (freq multiplied, amp/phase preserved), render N
samples at sample rate SR.

Per output sample i (i = 0..N-1), time `t = i/SR`:
`y[i] = sum over partials of amp * cos(2*pi*(R*freq)*t + phase)`.
Then apply a **half-hann edge fade**: for i < F where F=N//4,
`y[i] *= 0.5 - 0.5*cos(pi*(i+1)/F)`; for i >= N-F,
`y[i] *= 0.5 - 0.5*cos(pi*(N-i)/F)`. DC removal last:
subtract mean(y) once.
Output `{"samples":[...]}` each rounded 6 decimals, plus
`{"peak":max(|y|) 6 decimals, "freqs":[R*freq...]}` preserving input
partial order, freqs rounded 3 decimals.

## Automated controls (optional)

An input may add `"controls":[{...}]` and `"interp":"linear"|"hold"`
(default `"linear"`). Controls:

- `"at"`: **absolute sample index**; fractional values snap to the nearest
  sample and clamp to `[0, frames]`, so event jitter never changes which
  sample is affected.
- `"ratio"`: frequency multiplier from that sample onward.
- `"amp"`: global gain multiplier from that sample onward.
- `"reset":true` plus `"phase"`: re-anchor oscillator phase at that exact
  sample (inherited per-partial phase added, matching the static formula).
  Points without `"reset"` never touch phase.

Sample 0 is a virtual control holding the input `ratio` and gain 1.0, so
linear interpolation ramps smoothly into the first explicit point; `"hold"`
keeps each value until the next point. Events on the same sample apply in
input order (later wins), independent of sort stability. Phase is integrated
on the sample clock, `theta[i+1] = theta[i] + 2*pi*ratio(i)*freq/SR`, and
silence does not stop it, so mute/unmute introduces no phase discontinuity.

Checkpoints: `pitch.py in.json out.json --block N --checkpoint state.json`
renders in N-sample chunks and saves a JSON resume state (note description,
absolute `next_index`, running phases, faded samples for the global DC
removal). Blocked output is verified equal to the continuous render.

## Renderer API and checkpointing

`pitch.py` exposes the renderer as functions as well as the CLI:

- `partial_sample(partial, R, SR, i)` / `voice_sample(...)` evaluate the
  oscillator at **absolute** sample `i`. Phase is a pure function of
  `i/SR`, so there is no mutable phase accumulator to advance, clamp, or
  reset: a wrap or re-entry at any sample reproduces the same waveform.
- `render(partials, R, SR, N)` returns the whole fade-then-DC-removed
  buffer and is exactly what the CLI writes.
- `render_range(..., start, stop)` renders any absolute slice of the same
  `N`-sample render (fade included, DC removal excluded).
- `checkpoint_state(...)` / `resume(state, stop)` / `finish(state)` carry
  playback across blocks. The state holds the immutable note description,
  the absolute `next_index`, the running oscillator phases (needed once a
  control envelope is active), and the faded samples accumulated so far;
  `finish` applies the single global DC removal and refuses an incomplete
  render. Because oscillator state is a deterministic function of absolute
  sample time, chunked playback is sample-for-sample identical to continuous
  playback, whether or not controls are present.

Tests: `python3 -m unittest discover -s tests`.
