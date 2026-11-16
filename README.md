# De-esser: band split, detection envelope, branch recombination

A band-split de-esser that attenuates only the sibilant band, detects from the
filtered signal (in-band energy *and* a low-band reference), keeps its attack/
release/enable state across blocks, and cross-fades bypass instead of swapping
branches.

```
python3 deess.py input.json output.json       # documented entry point
python3 deesser.py input.json output.json     # same, engine module
python3 deesser.py in.wav out.wav             # 16/24/32-bit PCM, mono/stereo
python3 -m unittest test_deesser -v           # acceptance tests (47)
python3 verify.py                             # numeric acceptance report
```

No third-party dependencies (pure Python 3.8+).

## What was broken, and what changed

| Symptom | Cause in the old script | Fix |
| --- | --- | --- |
| Whole high band pulled down, voice dull | one gain per *block* from a single whole-band energy ratio | linear-phase complementary split; gain multiplies only the high band |
| High band stays down after the sibilant | detector had no signal reference, envelope used the attack coefficient in both directions | detection from filtered band + low-band reference; separate release coefficient |
| Broadband hits (drums) trigger the de-esser | no reference energy in the detector | trigger requires band to beat the low band by `ref_offset_db` *and* the absolute threshold |
| Click at enable/bypass | branches swapped between samples | linear cross-fade to the delay-compensated dry signal, envelope keeps running |
| Output changes with block length | state advanced per block | state advances per sample; automation is time-stamped |
| Non-target band smeared | bands not delay matched | both branches share one FIR, exactly complementary |

## Job formats

### 1. Frame job (the documented `deess.py` contract)

```json
{"frames":[{"t":0.0,"low":0.002,"high":0.0005}, ...],
 "threshold": 2.0, "ratio": 4.0, "attack": 10.0, "release": 40.0}
```

Per-frame band energies on a `frame_ms` timeline (10 ms by default).
The documented maths is preserved:

* `det = high / max(low, 1e-9)`, and `0` when both bands are zero;
* `over = det - threshold` when `det > threshold`, else `0`;
* `want = min(1.0, max(1/ratio, 1 - over*ratio))`;
* attack while `want < gain`, release while `want > gain`, each with the
  per-frame step `1 - exp(-frame_ms / time_ms)`.

Output: `{"frames":[{"t","det","gain"}...],"active_frames":N}` with `det` to
3 and `gain` to 4 decimals; `active_frames` counts frames whose *smoothed*
gain is below `0.99` (the old script counted raw `want`, so it reported frames
that had not started reducing yet).

Optional keys: `enabled`, `frame_ms`, `max_reduction_db`, `fade_ms`,
`smooth_ms`, `automation`/`bypass` lists:
`{"param":"ratio","value":8.0,"t":30}` / `{"enabled":false,"t":45}` where `t`
is a frame index. Scheduling into the past is rejected, not silently ignored.

### 2. Signal job (band split actually runs)

```json
{"audio":[[0.01],[0.02], ...], "sample_rate":48000, "channels":1,
 "block_size":64, "meter_ms":5,
 "params":{"split_hz":6000,"threshold_db":-34,"ratio":4,
           "ref_offset_db":5,"max_reduction_db":18,"attack_ms":0.5,
           "release_ms":60,"fade_ms":8,"detector_ms":1,"smooth_ms":5},
 "automation":[{"param":"ratio","value":8.0,"t":1.5}],
 "bypass":[{"enabled":false,"t":2.0},{"enabled":true,"t":2.5}]}
```

`block_size` only affects grouping - the output is bit-identical for any
partition (verified for 1, 3, 17, 64, 997 samples and the whole file).
Result includes `latency_samples`, the final `parameters`, all output
`frames`, and a `meter` list (`t`, `band_db`, `ref_db`, `gain_db`, `mix`) when
`meter_ms` is set.

#### Supplying filter coefficients ("task package")

`coefficients` replaces the designed split and accepts:

* a sequence — the low-pass prototype (validated symmetric, DC-normalised);
* `{"lowpass": [...]}` / `{"lp": [...]}` — the same;
* `{"highpass": [...]}` / `{"hp": [...]}`, or a bare kernel that passes
  Nyquist and blocks DC — converted to the exactly complementary low-pass
  `lp = unit[D] - hp`, so the supplied numbers still define the split and the
  two branches stay delay matched.

The kernel must be odd length and symmetric; anything else raises
`DeEsserError` rather than silently detuning the crossover. On the command
line, pass a JSON file: `--coefficients coefs.json` (either a bare list or an
object with a `lowpass`/`highpass` entry).

## Processing

Band split (linear-phase FIR, `D = (taps-1)/2`, 128 samples at 48 kHz):

```
low  = FIR_lp(x)
high = x[n-D] - low          # low + high == x[n-D] exactly
out  = low + g*high          # + mix ramp for bypass
```

The kernel is specified in time (5.35 ms), so `taps` scales with the sample
rate (237 @ 44.1 k, 257 @ 48 k, 515 @ 96 k) and the crossover keeps the same
relative width; pass an explicit odd `taps` to pin it. `g == 1` reproduces the
delayed input bit for bit, so the non-target band is not merely "less
affected" - it is untouched, and the output is simply delayed by `D`.

Detector (on the filtered branches, averaged over `detector_ms`):

```
threshold = max(threshold_db, ref_db + ref_offset_db)
over      = band_db - threshold
reduction = min(max_reduction_db, over * (1 - 1/ratio))
```

The reference comes from the same channel that provides the band energy, so a
loud bass in one channel cannot mask a sibilant in the other; the gain itself
is shared across channels (linked stereo).

Envelope: `attack_ms` while the gain falls, `release_ms` while it rises, with
sample-rate coefficients `1 - exp(-1/(ms*fs/1000))`. Release is what brings
the high band straight back when the sibilant ends.

Switching: `mix` ramps over `fade_ms` between 1 (processed) and 0 (dry).
Detector and envelope never stop, so state is continuous across a switch;
`out = dry + mix*(g-1)*high` cannot step because `mix` and `g` are ramped.

Automation: parameter steps are smoothed over `smooth_ms` and applied at the
scheduled sample, so a hard automation edge cannot click either.

## Acceptance report

`python3 verify.py` prints (values at 48 kHz, defaults):

| Scenario | Measurement | Value |
| --- | --- | --- |
| Synthetic sibilant | 7-12 kHz change | about -12 dB |
| | tail error vs delayed dry | 0.0 (bit exact) |
| | reduction 300 ms after the sibilant | about 0 dB |
| Broadband drum | max reduction | under 1 dB |
| | low band (100-4000 Hz) change | under 0.1 dB |
| Normal voice | voiced body error | 0.0 (bit exact) |
| | body band changes (150-4000 Hz) | under 0.05 dB |
| | sibilant band change | about -6.6 dB |
| Abrupt automation | LF residual at bypass switch | ~2e-5 (instant swap: ~1.5e-2) |
| | LF residual at parameter step | ~6e-6 |
| | reduction before / after a ratio step | ~-4.7 dB / -18 dB (ceiling) |
| Structure | block-size difference | 0.0 |
| | split complementarity error | 0.0 |

`python3 -m unittest test_deesser -v` runs the same scenarios as assertions,
including reference-energy veto, stereo linking, state continuity, supplied
coefficients and both CLI paths (47 tests).

## Who gets the gain

Only the high band is scaled, and the gain is computed from the high band of
the band split - so the reduction follows the sibilant sample by sample
instead of following a block statistic. That is what removes both the
"whole band down" dullness and the "still dull afterwards" tail.
