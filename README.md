# Multi-crossover speaker band router

Run: `python3 xover.py input.json output.json`

Input `{"xovers":[X1,X2],"bands":[{"name","range":[lo,hi],
"driver"}...],"delay_ms":{driver:ms},"sr":SR,"signal":[...]}` —
Linkwitz-style 3-way split.

## Filter model and complementary synthesis

Per sample each crossover edge performs exactly one state update

    p <- a*u + (1-a)*p,  LP = p,  HP = u - p,  LP + HP = u

with `a = 2*pi*fc/SR / (1 + 2*pi*fc/SR)`. The LP output closes the
lower branch while the HP remainder continues upward, so for a 3-way
split `low + mid + high = x` at every sample (verified to < 1e-12).
The top band is the final HP remainder with no closing low-pass, per
the band ranges. Each edge keeps one shared state; LP and HP are
complementary, which removes the in-band cancellation between the
woofer and the main channel.

## Delay and polarity

`delay_ms` is applied per driver as a true RIGHT shift
`y[n] = x[n-d]`, `d = round(ms*SR/1000)`, with a zero-filled head
(ring buffer). The previous implementation shifted LEFT, which made
the driver arrive early and cancelled the main channel inside the
crossover band. Optional `"polarity": {driver: 1|-1}` flips a branch.

## Crossover migration events

Optional `"events": [{"at": <absolute sample>, "xover": <edge index>,
"fc": <new Hz>}]` — or `"a": <coefficient>` to migrate a supplied
filter coefficient directly — plus optional per-event `"polarity"`
and `"delay_ms"` maps. The event is applied at exactly sample `at`:
filter state `p` and delay-line history are preserved, so every
branch stays continuous across the switch (no reset/click). The same
events produce byte-identical output for any processing block size.

## Multi-channel

A `signal` of plain numbers is mono; a list of equal-length lists is
multi-channel (e.g. `[L, R]`) with independent per-channel states.

## Output

Legacy mono input keeps the original schema:
`{"bands":[{"driver","name","samples":[...]}...], "energy_in",
"energy_out"}`, samples rounded to 5 decimals. Event / coefficient /
multi-channel inputs additionally return:

* `mix` — per-sample synthesis response (sum of aligned branches);
* `edges` — final per-edge coefficient / cutoff table;
* `state` — full monitor snapshot (cursor, edge states, per-branch
  polarity/delay and ring-buffer history, remaining events), which can
  be fed to `Engine.restore_state` to rebuild processing and continue
  under a different block layout.

## Verification

`python3 verify_xover.py` runs 54 checks: pulse reconstruction, sine
sweep with a sample-accurate crossover switch, fixed-phase stereo,
delay/polarity cancellation, block-size invariance (block sizes
1..N), and monitor snapshot/restore.

