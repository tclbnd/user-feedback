"""Extra robustness tests beyond the minimum acceptance suite."""

import math
import random

from stretch import render, StretchEngine, TimeMap
from test_acceptance import (find_pulses, estimate_frequency, pearson,
                             split_blocks, RATE, TAU)


def mixed_signal(n):
    """Sine bed + amplitude swell + several sharp drum pulses."""
    frames = []
    hits = {700: 1.0, 2100: 1.0, 3300: 0.9, 4700: 1.0}
    for i in range(n):
        bed = 0.35 * math.sin(TAU * 240 * i / RATE)
        swell = 0.2 * math.sin(TAU * 3.0 * i / n) * math.sin(
            TAU * 120 * i / RATE)
        drum = 0.0
        for st, amp in hits.items():
            if st <= i < st + 200:
                d = i - st
                drum += amp * math.exp(-d / 40.0) * math.sin(
                    TAU * 300 * d / RATE)
        v = bed + swell + drum
        frames.append([v, -0.8 * v])
    return frames, hits


def test_many_seeds():
    n = 5500
    stereo, hits = mixed_signal(n)
    knots_list = [
        [[0, 0.7], [n, 1.6]],
        [[0, 1.6], [n // 2, 0.7], [n, 1.6]],
        [[0, 1.0]],
        [[0, 0.7], [n // 3, 0.7], [2 * n // 3, 1.6], [n, 1.6]],
    ]
    for ki, knots in enumerate(knots_list):
        ref = render({"rate": RATE, "blocks": [list(stereo)],
                      "knots": knots})
        ref_frames = ref["frames"]
        ref_pulses = find_pulses([f[0] for f in ref_frames], RATE)
        for seed in range(5):
            rng = random.Random(1000 * ki + seed)
            sizes, total = [], 0
            choices = [1, 1, 2, 5, 11, 29, 60, 130, 300]
            while total < n:
                sz = rng.choice(choices)
                sizes.append(sz)
                total += sz
            chunks = split_blocks(stereo, sizes)
            eng = StretchEngine(RATE, knots)
            got = []
            for ch in chunks:
                got.extend(eng.process(ch))
            got.extend(eng.flush())
            nout = eng.tm.nout
            got = (got + [[0.0, 0.0]] * nout)[:nout]
            assert len(got) == len(ref_frames)
            md = max(abs(got[i][c] - ref_frames[i][c])
                     for i in range(nout) for c in (0, 1))
            pulses = find_pulses([f[0] for f in got], RATE)
            assert md == 0.0, (ki, seed, md)
            assert pulses == ref_pulses, (ki, seed, pulses, ref_pulses)
        corr = pearson([f[0] for f in ref_frames],
                       [f[1] for f in ref_frames])
        print(f"[S{ki}] knots={knots[0]}.. nout={len(ref_frames)} "
              f"5 seeds bit-identical, pulses={len(ref_pulses)}, "
              f"stereo corr={corr:.4f}")
        assert corr <= -0.995


def test_unit_rate_identity_quality():
    # v=1.0 must reconstruct a clean sine with negligible distortion
    n = 4000
    f0 = 320.0
    src = [[0.5 * math.sin(TAU * f0 * i / RATE),
            0.5 * math.sin(TAU * f0 * i / RATE)] for i in range(n)]
    out = render({"rate": RATE, "blocks": [src], "knots": [[0, 1.0]]})
    fe = estimate_frequency([f[0] for f in out["frames"]], RATE,
                            step=0.25)
    # measure amplitude on local maxima of |signal| (envelope), not at the
    # zero crossings where |value| naturally collapses to 0
    sig = [f[0] for f in out["frames"]]
    period = RATE / f0
    peaks = []
    i = 1
    while i < len(sig) - 1:
        if abs(sig[i]) >= abs(sig[i - 1]) and abs(sig[i]) >= abs(sig[i + 1]):
            peaks.append(abs(sig[i]))
            i += max(1, int(period * 0.75))
        else:
            i += 1
    inner_peaks = peaks[2:-2]
    amp_err = max(abs(p - 0.5) for p in inner_peaks)
    mean_amp = sum(inner_peaks) / len(inner_peaks)
    print(f"[U] v=1.0 sine: f={fe:.2f} Hz (target {f0}), "
          f"envelope mean={mean_amp:.4f} max peak err={amp_err:.4f}")
    assert abs(fe - f0) / f0 <= 0.01
    assert amp_err < 0.05


def test_theory_length_extremes():
    # compare engine nout against an independent direct integration
    n = 2000
    for v0, v1 in ((0.7, 1.6), (1.6, 0.7), (0.7, 0.7), (1.6, 1.6)):
        knots = [[0, v0], [n, v1]]
        src = [[[0.1, -0.1]] * n]
        out = render({"rate": RATE, "blocks": src, "knots": knots})
        # independent integration
        x = 0.0
        t = 0
        while x < n:
            va = v0 + (v1 - v0) * (t / n) if t < n else v1
            vb = v0 + (v1 - v0) * ((t + 1) / n) if (t + 1) < n else v1
            x += 0.5 * (va + vb)
            t += 1
        assert abs(len(out["frames"]) - t) <= 1, (v0, v1)
        print(f"[L] v {v0}->{v1}: engine nout={len(out['frames'])} "
              f"independent={t} OK")


def test_monotone_mapping_and_transients():
    n = 5000
    stereo, hits = mixed_signal(n)
    knots = [[0, 0.7], [n, 1.6]]
    out = render({"rate": RATE, "blocks": [stereo], "knots": knots})
    m = out["mapping"]
    assert all(m[i] <= m[i + 1] + 1e-12 for i in range(len(m) - 1))
    # every reported transient maps monotonically and inside range
    tm = TimeMap(knots)
    tm.finalize(n)
    for src_i, out_i in out["transients"]:
        assert 0 <= src_i < n and 0 <= out_i < len(out["frames"])
        assert abs(tm.x_at(out_i) - src_i) <= max(1.0, tm.speed(out_i))
    print(f"[M] mapping monotone; {len(out['transients'])} transients "
          f"all in range and consistent with the time map")


if __name__ == "__main__":
    test_many_seeds()
    test_unit_rate_identity_quality()
    test_theory_length_extremes()
    test_monotone_mapping_and_transients()
    print("\nALL STRESS TESTS PASSED")
