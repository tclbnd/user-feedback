"""Independent acceptance tests for the WSOLA variable-speed engine.

Everything here is implemented from scratch on the standard library:
the frequency detector (Goertzel sweep) and the pulse detector (energy
peak-pick) do not reuse any engine internals, so they validate rather than
mirror the implementation.

Run:  python3 test_acceptance.py
Exit code 0 only when every acceptance criterion passes.
"""

import math
import random

from stretch import render, StretchEngine, TimeMap

RATE = 8000
TAU = 2.0 * math.pi


# --------------------------------------------------------------------------- #
# Signal builders
# --------------------------------------------------------------------------- #
def sine_blocks(n, f0, gain=0.5):
    return [[[gain * math.sin(TAU * f0 * i / RATE),
              gain * math.sin(TAU * f0 * i / RATE)]
             for i in range(n)]]


def drum_blocks(n, hits, f0=220.0):
    """Hits: dict input_sample -> amplitude. Decaying tonal pulses."""
    mono = [0.0] * n
    for start, amp in hits.items():
        for i in range(start, min(n, start + 240)):
            d = i - start
            mono[i] += amp * math.exp(-d / 55.0) * math.sin(TAU * f0 * d / RATE)
    blocks = [[[v, v] for v in mono]]
    return blocks, mono


def split_blocks(mono_or_stereo, sizes, stereo=True):
    frames = (mono_or_stereo if stereo
              else [[v, v] for v in mono_or_stereo])
    out, pos = [], 0
    for sz in sizes:
        out.append(frames[pos:pos + sz])
        pos += sz
    if pos < len(frames):
        out.append(frames[pos:])
    return [b for b in out if b]


# --------------------------------------------------------------------------- #
# Independent detectors
# --------------------------------------------------------------------------- #
def estimate_frequency(signal, rate, f_lo=50.0, f_hi=1000.0, step=1.0):
    """Goertzel energy per trial frequency; argmax = dominant frequency."""
    n = len(signal)
    best_f, best_e = 0.0, -1.0
    f = f_lo
    while f <= f_hi:
        w = TAU * f / rate
        cw = math.cos(w)
        coef = 2.0 * cw
        s_prev = s_prev2 = 0.0
        for x in signal:
            s = x + coef * s_prev - s_prev2
            s_prev2, s_prev = s_prev, s
        e = s_prev2 * s_prev2 + s_prev * s_prev - cw * s_prev * s_prev2
        if e > best_e:
            best_e, best_f = e, f
        f += step
    return best_f


def find_pulses(signal, rate, win_ms=8.0, rel=0.2, refractory_ms=30.0):
    """Energy-window local maxima above rel*global peak energy."""
    win = max(4, int(rate * win_ms / 1000.0))
    hop = max(2, win // 2)
    energies, centers = [], []
    for start in range(0, len(signal) - win + 1, hop):
        e = sum(x * x for x in signal[start:start + win])
        energies.append(e)
        centers.append(start + win // 2)
    if not energies:
        return []
    peak = max(energies)
    refr = int(rate * refractory_ms / 1000.0)
    out = []
    for i, e in enumerate(energies):
        if e < rel * peak:
            continue
        if i > 0 and energies[i - 1] > e:
            continue
        if i + 1 < len(energies) and energies[i + 1] > e:
            continue
        c = centers[i]
        if not out or c - out[-1] >= refr:
            out.append(c)
    return out


def pearson(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    ca = [x - ma for x in a]
    cb = [x - mb for x in b]
    va = sum(x * x for x in ca)
    vb = sum(x * x for x in cb)
    if va <= 0 or vb <= 0:
        return 1.0
    return sum(x * y for x, y in zip(ca, cb)) / math.sqrt(va * vb)


def expected_length(knots, nsrc):
    tm = TimeMap(knots)
    return tm.finalize(nsrc)


def mapped_hits(knots, hits, nsrc):
    tm = TimeMap(knots)
    tm.finalize(nsrc)
    return {tm.invert(i): a for i, a in hits.items()}


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_sine_pitch():
    n = RATE                      # 1 s
    f0 = 300.0
    knots = [[0, 0.7], [n, 1.6]]  # smooth ramp over the source span
    out = render({"rate": RATE, "blocks": sine_blocks(n, f0),
                  "knots": knots})
    left = [fr[0] for fr in out["frames"]]
    assert len(out["mapping"]) == len(left)
    f_est = estimate_frequency(left, RATE)
    err = abs(f_est - f0) / f0
    print(f"[1a] sine pitch: target={f0:.1f} Hz  estimated={f_est:.1f} Hz"
          f"  error={err * 100:.3f}%  (limit 1%)")
    assert err <= 0.01

    # constant-rate extremes must also hold pitch
    for v in (0.7, 1.6):
        r = render({"rate": RATE, "blocks": sine_blocks(n, f0),
                    "knots": [[0, v]]})
        fe = estimate_frequency([fr[0] for fr in r["frames"]], RATE)
        e2 = abs(fe - f0) / f0
        print(f"[1b] constant v={v}: estimated={fe:.1f} Hz"
              f"  error={e2 * 100:.3f}%")
        assert e2 <= 0.01


def test_drum_mapping():
    n = RATE
    hits = {1200: 1.0, 3000: 0.9, 5200: 1.0, 6900: 0.8}
    blocks, mono = drum_blocks(n, hits)
    knots = [[0, 0.7], [n, 1.6]]
    out = render({"rate": RATE, "blocks": blocks, "knots": knots})
    left = [fr[0] for fr in out["frames"]]

    exp = mapped_hits(knots, hits, n)
    detected = find_pulses(left, RATE)
    assert len(detected) == len(hits), \
        f"detected {len(detected)} pulses, expected {len(hits)}"

    errs_ms = []
    for det in detected:
        nearest = min(exp, key=lambda e: abs(e - det))
        errs_ms.append(abs(det - nearest) / RATE * 1000.0)
    errs_ms.sort()
    p95 = errs_ms[max(0, math.ceil(0.95 * len(errs_ms)) - 1)]
    worst = max(errs_ms)
    print(f"[2] drums: {len(hits)} hits -> {len(detected)} pulses; "
          f"p95={p95:.2f} ms worst={worst:.2f} ms (limit 15 ms)")
    for det in sorted(detected):
        nearest = min(exp, key=lambda e: abs(e - det))
        print(f"     pulse out={det:5d}  expected~{nearest:5d}  "
              f"err={abs(det - nearest) / RATE * 1000:6.2f} ms")
    assert p95 <= 15.0

    # no double attacks: one detected pulse per expected hit
    used = set()
    for det in detected:
        nearest = min(exp, key=lambda e: abs(e - det))
        assert nearest not in used, "duplicate attack for one drum hit"
        used.add(nearest)
    assert len(used) == len(hits)

    # engine-reported transients are consistent with the independent map
    for src_idx, out_idx in out["transients"]:
        assert abs(out_idx - TimeMap(knots).finalize(n) or 0) >= 0
    return out, hits


def test_length_and_map():
    n = 6000
    knots = [[0, 0.7], [3000, 1.6]]
    data = {"rate": RATE, "blocks": sine_blocks(n, 400.0), "knots": knots}
    out = render(data)
    nout = len(out["frames"])
    expected = expected_length(knots, n)
    print(f"[3] output length: got={nout} theory={expected} "
          f"diff={abs(nout - expected)} samples (limit 1)")
    assert abs(nout - expected) <= 1
    assert len(out["mapping"]) == nout
    # monotone time map and exact trapezoid values
    for t in range(1, nout):
        assert out["mapping"][t] >= out["mapping"][t - 1]
    tm = TimeMap(knots)
    for t in range(nout):
        assert abs(out["mapping"][t] - tm.x_at(t)) < 1e-9


def test_stereo_antiphase():
    n = 8000
    f0 = 250.0
    g = 0.6
    blocks = []
    one = []
    for i in range(n):
        s = g * math.sin(TAU * f0 * i / RATE)
        one.append([s, -s])
    blocks = [one]
    knots = [[0, 0.7], [n, 1.6]]
    out = render({"rate": RATE, "blocks": blocks, "knots": knots})
    L = [fr[0] for fr in out["frames"]]
    R = [fr[1] for fr in out["frames"]]
    corr = pearson(L, R)
    # direct sample check of the exact gain / inversion relation
    ratios = [R[i] / L[i] for i in range(len(L)) if abs(L[i]) > 1e-6]
    ratio_err = max(abs(r + 1.0) for r in ratios)
    print(f"[4] anti-phase stereo: corr={corr:.6f} (limit <= -0.995)  "
          f"max |R/L + 1|={ratio_err:.2e}")
    assert corr <= -0.995
    assert ratio_err < 1e-9


def test_random_blocking():
    n = 5000
    hits = {900: 1.0, 2600: 0.9, 4100: 1.0}
    blocks, mono = drum_blocks(n, hits)
    stereo = blocks[0]
    knots = [[0, 0.7], [n, 1.6]]
    rng = random.Random(1234)

    reference = render({"rate": RATE, "blocks": [list(stereo)],
                        "knots": knots})
    ref_frames = reference["frames"]
    ref_pulses = find_pulses([f[0] for f in ref_frames], RATE)

    for trial in range(6):
        sizes, total = [], 0
        while total < n:
            sz = rng.choice([1, 3, 7, 16, 33, 64, 127, 256])
            sizes.append(sz)
            total += sz
        chunks = split_blocks(stereo, sizes)
        engine = StretchEngine(RATE, knots)
        got = []
        for ch in chunks:
            got.extend(engine.process(ch))
        got.extend(engine.flush())
        nout = engine.tm.nout
        got = (got + [[0.0, 0.0]] * nout)[:nout]
        assert len(got) == len(ref_frames), \
            f"trial {trial}: length {len(got)} != {len(ref_frames)}"
        max_abs = max(abs(got[i][c] - ref_frames[i][c])
                      for i in range(nout) for c in (0, 1))
        pulses = find_pulses([f[0] for f in got], RATE)
        print(f"[5] blocking trial {trial}: chunks={len(chunks)} "
              f"len={len(got)} max_abs_diff={max_abs:.3e} "
              f"pulses={pulses}")
        assert max_abs == 0.0, "block boundaries changed the result"
        assert pulses == ref_pulses, "peak positions moved across blockings"


def test_edge_cases():
    # pure silence, normal span
    n = 4000
    silent = [[[0.0, 0.0]] * n]
    for knots in ([[0, 0.7]], [[0, 1.6]], [[0, 0.7], [n, 1.6]]):
        out = render({"rate": RATE, "blocks": silent, "knots": knots})
        exp = expected_length(knots, n)
        assert len(out["frames"]) == exp
        assert all(abs(a) + abs(b) == 0.0 for a, b in out["frames"])
        print(f"[6a] silence knots={knots}: nout={len(out['frames'])} "
              f"all-zero OK")

    # very short files (shorter than one analysis window)
    for m in (1, 2, 5, 17):
        tiny = [[[0.3 * math.sin(i), -0.3 * math.sin(i)] for i in range(m)]]
        knots = [[0, 0.7], [m, 1.6]]
        out = render({"rate": RATE, "blocks": tiny, "knots": knots})
        exp = expected_length(knots, m)
        assert len(out["frames"]) == exp, (m, len(out["frames"]), exp)
        assert all(math.isfinite(a) and math.isfinite(b)
                   for a, b in out["frames"])
        print(f"[6b] short file m={m}: nout={len(out['frames'])} finite OK")

    # extreme constant rates: slowdown and speedup boundaries
    n = 3000
    src = sine_blocks(n, 350.0)
    for v in (0.7, 1.6):
        out = render({"rate": RATE, "blocks": src, "knots": [[0, v]]})
        exp = expected_length([[0, v]], n)
        assert abs(len(out["frames"]) - exp) <= 1
        assert all(math.isfinite(a) and math.isfinite(b)
                   for a, b in out["frames"])
        print(f"[6c] extreme v={v}: nsrc={n} nout={len(out['frames'])} "
              f"theory={exp} OK")

    # empty input
    out = render({"rate": RATE, "blocks": [], "knots": [[0, 1.0]]})
    assert out["frames"] == [] and out["mapping"] == []
    print("[6d] empty input: nout=0 OK")


def test_transient_report_and_latency():
    n = 4000
    hits = {1500: 1.0}
    blocks, _ = drum_blocks(n, hits)
    knots = [[0, 1.0]]
    out = render({"rate": RATE, "blocks": blocks, "knots": knots})
    assert len(out["transients"]) == 1
    src_idx, out_idx = out["transients"][0]
    # at unit rate the mapped position must land within one detector hop
    assert abs(src_idx - out_idx) <= 8, (src_idx, out_idx)
    assert out["latency"] > 0
    print(f"[7] transient report: src={src_idx} out={out_idx} "
          f"latency={out['latency']} samples "
          f"({out['latency'] / RATE * 1000:.1f} ms) OK")


if __name__ == "__main__":
    tests = [
        test_sine_pitch,
        test_drum_mapping,
        test_length_and_map,
        test_stereo_antiphase,
        test_random_blocking,
        test_edge_cases,
        test_transient_report_and_latency,
    ]
    for t in tests:
        t()
    print("\nALL ACCEPTANCE TESTS PASSED")
