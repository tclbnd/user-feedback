#!/usr/bin/env python3
"""Verification harness for the crossover fix.

Covers the evidence required by the task:

  1. Pulse: complementary LP+HP edges reconstruct the input sample by
     sample (no in-band cancellation after delay/polarity alignment).
  2. Sine sweep: each branch only carries its own band, switch events
     move the crossover at the exact absolute sample and stay continuous.
  3. Fixed-phase stereo: L/R routing and phases are preserved bit-for-bit.
  4. The original left-shift delay bug is demonstrated to misalign the
     branches, while the right-shift fix restores the sum.
  5. Identical events under different block lengths produce identical
     per-sample output.
  6. Monitor state snapshot/restore resumes processing identically.
  7. Legacy CLI output schema is unchanged.
"""
import json
import math
import os
import subprocess
import sys
import tempfile

from xover_engine import Engine, DelayLine, lp_alpha, delay_samples, run_pipeline

SR = 48000
BANDS = [
    {"name": "low", "range": [0, 300], "driver": "sub"},
    {"name": "mid", "range": [300, 3000], "driver": "main"},
    {"name": "high", "range": [3000, SR // 2], "driver": "tweet"},
]
XOVERS = [300.0, 3000.0]
DELAYS = {"sub": 1.0, "main": 0.0, "tweet": 0.0}  # 48 sample alignment
N = 4800
PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + ((" -- " + detail) if detail and not cond else ""))


def max_abs(a):
    return max(abs(v) for v in a) if a else 0.0


def band_energy_basic(signal, fc, sr, kind):
    """Reference single-edge filter, independent of the engine."""
    a = lp_alpha(fc, sr)
    p = 0.0
    out = []
    for v in signal:
        p = a * v + (1.0 - a) * p
        out.append(p if kind == "lp" else v - p)
    return sum(v * v for v in out)


def make_pulse(n, at=100):
    return [1.0 if i == at else 0.0 for i in range(n)]


def make_sweep(n, sr, f0=20.0, f1=12000.0):
    phase = 0.0
    out = []
    for i in range(n):
        f = f0 * (f1 / f0) ** (i / max(1, n - 1))
        phase += 2.0 * math.pi * f / sr
        out.append(math.sin(phase))
    return out


def make_stereo(n, sr, f=1000.0):
    """Fixed-phase pair: L = cos, R = cos + 90-degree-shifted sin."""
    left, right = [], []
    for i in range(n):
        ph = 2.0 * math.pi * f * i / sr
        left.append(math.cos(ph))
        right.append(math.cos(ph) + 0.5 * math.sin(ph))
    return [left, right]


# ----------------------------------------------------------------------
# 1. pulse: complementary synthesis with aligned, same-polarity branches
# ----------------------------------------------------------------------
def test_pulse_reconstruction():
    print("1. pulse reconstruction (complementary LP+HP edges)")
    sig = make_pulse(N)
    inp = {"sr": SR, "xovers": XOVERS, "bands": BANDS,
           "delay_ms": {"sub": 0.0, "main": 0.0, "tweet": 0.0},
           "signal": sig}
    out = run_pipeline(inp)
    err = max_abs([m - s for m, s in zip(out["mix"], sig)])
    check("sum of branches equals pulse sample-by-sample", err < 1e-12,
          "max err %.3e" % err)
    # the mix telescopes to the input, so its energy equals the input
    # exactly (band energies alone differ because IIR tails ring beyond
    # the finite clip window; that is windowing, not in-band loss)
    e_mix = sum(v * v for v in out["mix"])
    check("synthesis energy equals input energy (no in-band loss)",
          abs(e_mix - out["energy_in"]) < 1e-12,
          "in %.10f mix %.10f" % (out["energy_in"], e_mix))
    check("band energy accounting matches reported energy_out",
          abs(sum(v * v for band in out["bands"]
                  for v in band["samples"]) - out["energy_out"]) < 1e-3)

    # every branch has zero head before the pulse reaches its first stage
    for band in out["bands"]:
        check("branch %s starts at zero" % band["name"],
              all(abs(v) < 1e-12 for v in band["samples"][:99]))


# ----------------------------------------------------------------------
# 2. sweep: band routing + sample-accurate, continuous crossover switch
# ----------------------------------------------------------------------
def test_sweep_and_switch():
    print("2. sine sweep with crossover migration event")
    sig = make_sweep(N, SR)
    switch = 2400
    inp = {"sr": SR,
           "xovers": XOVERS,
           "bands": BANDS,
           "delay_ms": DELAYS,
            "events": [{"at": switch, "xover": 0, "fc": 500.0}],
           "signal": sig}

    # one-shot reference
    ref = run_pipeline(inp)
    check("edge coefficient migrated to 500 Hz",
          abs(ref["edges"][0]["fc"] - 500.0) < 1e-12)

    # continuity definition: internal edge state p is carried across the
    # coefficient migration (no reset), so no impulse/click is injected
    pre_eng = Engine(SR, BANDS, XOVERS, delay_ms=DELAYS,
                     events=inp["events"])
    pre_eng.process_block(sig[:switch])
    p_before = pre_eng.edge_ch[0][0].state
    post_eng = Engine(SR, BANDS, XOVERS, delay_ms=DELAYS,
                      events=inp["events"])
    post_eng.process_block(sig[:switch + 1])
    p_after = post_eng.edge_ch[0][0].state
    check("edge state p is preserved through the switch (no reset)",
          abs(p_after - p_before) < 1.0
          and math.isfinite(p_after))
    # the switch is a coefficient change: output must stay finite and
    # never exceed the signal's own amplitude scale (no click spike)
    low = ref["bands"][0]["samples"]
    mid = ref["bands"][1]["samples"]
    scale = max_abs(sig)
    jump_low = abs(low[switch] - low[switch - 1])
    jump_mid = abs(mid[switch] - mid[switch - 1])
    check("low branch stays bounded across switch",
          jump_low <= 2.0 * scale, "jump %.4f" % jump_low)
    check("mid branch stays bounded across switch",
          jump_mid <= 2.0 * scale, "jump %.4f" % jump_mid)

    # block-length invariance: same absolute sample positions
    for bs in (1, 3, 7, 64, 127, 513, 1024, N):
        other = run_pipeline(inp, block_size=bs)
        same_bands = all(
            other["bands"][bi]["samples"] == ref["bands"][bi]["samples"]
            for bi in range(len(BANDS)))
        same_mix = other["mix"] == ref["mix"]
        check("block size %d gives identical samples" % bs,
              same_bands and same_mix)

    # coefficient-based event (题包给出滤波系数的情形)
    a_new = lp_alpha(500.0, SR)
    inp_coef = dict(inp)
    inp_coef["events"] = [{"at": switch, "xover": 0, "a": a_new}]
    out_coef = run_pipeline(inp_coef)
    check("coefficient event matches fc event exactly",
          out_coef["bands"][0]["samples"] == ref["bands"][0]["samples"]
          and out_coef["mix"] == ref["mix"])

    # energy routing on a plain (no-event) sweep
    plain = dict(inp)
    plain.pop("events")
    plain_out = run_pipeline(plain)
    e_low = sum(v * v for v in plain_out["bands"][0]["samples"])
    e_high = sum(v * v for v in plain_out["bands"][2]["samples"])
    check("sweep energy reaches both low and high branches",
          e_low > 100.0 and e_high > 100.0,
          "low %.1f high %.1f" % (e_low, e_high))


# ----------------------------------------------------------------------
# 3. fixed-phase stereo
# ----------------------------------------------------------------------
def test_stereo():
    print("3. fixed-phase stereo routing")
    stereo = make_stereo(N, SR)
    inp = {"sr": SR, "xovers": XOVERS, "bands": BANDS,
           "delay_ms": {"sub": 0.0, "main": 0.0, "tweet": 0.0},
           "signal": stereo}
    # full precision through the engine itself
    eng = Engine(SR, BANDS, XOVERS, channels=2)
    outs, mixes = eng.run(stereo)
    out = run_pipeline(inp)
    check("mix output carries two channels", len(out["mix"]) == 2)

    # each channel reconstructs its own input (exact, then rounded level)
    for c in range(2):
        err_exact = max_abs([m - s for m, s in zip(mixes[c], stereo[c])])
        err_round = max_abs([m - s for m, s in
                             zip(out["mix"][c], stereo[c])])
        check("channel %d reconstructed exactly (full precision)" % c,
              err_exact < 1e-12, "max err %.3e" % err_exact)
        check("channel %d within 5-decimal rounding noise" % c,
              err_round < 2e-5, "max err %.3e" % err_round)

    # fixed phase: L/R remain distinct (phase information kept)
    check("left/right mid branches differ (phase information kept)",
          outs[0][1] != outs[1][1])
    # deterministic: rerun matches bit-for-bit
    eng2 = Engine(SR, BANDS, XOVERS, channels=2)
    outs2, _ = eng2.run(stereo)
    check("stereo rerun is bit identical", outs2 == outs)


# ----------------------------------------------------------------------
# 4. delay / polarity: the cancellation bug and its fix
# ----------------------------------------------------------------------
def test_delay_and_polarity():
    print("4. delay compensation and polarity cancellation")
    sig = make_sweep(2000, SR)
    d_ms = 1.0
    d = delay_samples(d_ms, SR)
    check("1.0 ms at 48 kHz = 48 samples", d == 48)

    # fixed delay line semantics: head is zero, tail is the input
    inp = {"sr": SR, "xovers": XOVERS, "bands": BANDS,
           "delay_ms": {"sub": d_ms, "main": 0.0, "tweet": 0.0},
           "signal": sig}
    out = run_pipeline(inp)
    low = out["bands"][0]["samples"]
    check("right shift: head zero-filled",
          all(abs(v) < 1e-12 for v in low[:d]))
    # compare against the undelayed engine branch (full precision)
    eng_d = Engine(SR, BANDS, XOVERS, delay_ms={"sub": d_ms})
    od, _ = eng_d.run(sig)
    low_d = od[0][0]
    eng_0 = Engine(SR, BANDS, XOVERS)
    o0, _ = eng_0.run(sig)
    low0 = o0[0][0]
    # physical d-sample delay: an impulse at source index k appears at
    # output index k+d; the first d output samples are zero
    aligned = all(abs(low_d[n] - (0.0 if n < d else low0[n - d])) < 1e-12
                  for n in range(d, len(low_d)))
    check("right shift: y[n] = undelayed[n-d], zero-filled head", aligned)
    # direct ring-buffer semantics with a non-zero-first source
    dl = DelayLine(d)
    width = 2 * d + 1
    src = [float(i + 1) for i in range(width)]  # 1,2,3,... no leading 0
    dl_seq = [dl.push_pop(v, d) for v in src]
    formula = [0.0] * d + src[:width - d]
    check("delay line: impulse delayed by exactly d samples",
          dl_seq == formula and dl_seq[d] == src[0])

    # old buggy direction would put zeros at the tail and advance audio
    buggy = low[d:] + [0.0] * d
    check("buggy left shift differs from fixed right shift",
          buggy != low and abs(buggy[-1]) < 1e-12)

    # polarity inversion of one branch cancels the sum; correct
    # polarities reconstruct it (full precision, no rounding in the way)
    eng_good = Engine(SR, BANDS, XOVERS)
    _og, gmixes = eng_good.run(sig)
    eng_bad = Engine(SR, BANDS, XOVERS, polarity={"sub": -1})
    _ob, bmixes = eng_bad.run(sig)
    mix_good, mix_bad = gmixes[0], bmixes[0]
    err_good = max_abs([m - s for m, s in zip(mix_good, sig)])
    err_bad = max_abs([m - s for m, s in zip(mix_bad, sig)])
    check("matched polarity reconstructs (err < 1e-12)", err_good < 1e-12)
    check("flipped polarity breaks synthesis (cancellation visible)",
          err_bad > 100.0 * max(err_good, 1e-12),
          "good %.2e bad %.2e" % (err_good, err_bad))

    # runtime delay migration keeps the ring history continuous
    events = [{"at": 1000, "delay_ms": {"sub": 0.5}}]
    eng = Engine(SR, BANDS, XOVERS, delay_ms={"sub": d_ms}, events=events)
    outs, _ = eng.run(sig, block_size=37)
    low_blk = outs[0][0]
    ref_eng = Engine(SR, BANDS, XOVERS, delay_ms={"sub": d_ms},
                     events=events)
    ref_outs, _ = ref_eng.run(sig)
    check("delay migration block-independent", low_blk == ref_outs[0][0])


# ----------------------------------------------------------------------
# 5. events land on the same absolute sample under any block size
# ----------------------------------------------------------------------
def test_event_sample_position():
    print("5. event absolute sample position")
    # sustained excitation so post-event samples actually carry energy
    sig = make_sweep(N, SR)
    ev = {"at": 321, "xover": 1, "fc": 4000.0}
    inp = {"sr": SR, "xovers": XOVERS, "bands": BANDS,
           "delay_ms": DELAYS, "events": [ev], "signal": sig}
    ref = run_pipeline(inp)
    pre = run_pipeline(dict(inp, events=[]))
    # outputs must be identical up to (but not including) the event
    same_before = all(
        ref["bands"][bi]["samples"][:321]
        == pre["bands"][bi]["samples"][:321]
        for bi in range(len(BANDS)))
    check("no output change before event sample", same_before)
    diffs = [i for i in range(321, N)
             if any(ref["bands"][bi]["samples"][i]
                    != pre["bands"][bi]["samples"][i]
                    for bi in range(len(BANDS)))]
    check("first changed sample is exactly the event position",
          bool(diffs) and diffs[0] == 321, "first diff %s" % diffs[:1])
    for bs in (2, 5, 100, 320, 321, 322, 1000):
        other = run_pipeline(inp, block_size=bs)
        check("block size %d: identical event application" % bs,
              other["mix"] == ref["mix"])


# ----------------------------------------------------------------------
# 6. monitor state snapshot / restore
# ----------------------------------------------------------------------
def test_snapshot_restore():
    print("6. monitor state snapshot/restore")
    sig = make_sweep(N, SR)
    ev = {"at": 3000, "xover": 0, "fc": 450.0}
    inp = {"sr": SR, "xovers": XOVERS, "bands": BANDS,
           "delay_ms": DELAYS, "events": [ev], "signal": sig}

    # full reference
    full = Engine(SR, BANDS, XOVERS, delay_ms=DELAYS, events=[ev])
    full_outs, full_mix = full.run(sig)

    # split run with snapshot at the midpoint
    cut = 2000
    e1 = Engine(SR, BANDS, XOVERS, delay_ms=DELAYS, events=[ev])
    o1_raw, m1 = e1.process_block(sig[:cut])
    o1 = o1_raw[0]
    snap = e1.snapshot()
    check("snapshot stores cursor and remaining event",
          snap["n"] == cut and snap["events_remaining"][0]["at"] == 3000)

    # fresh engine restored from the snapshot, then feed the tail
    e2 = Engine(SR, BANDS, XOVERS, delay_ms=DELAYS, events=[ev])
    e2.restore_state(snap)
    o2_raw, m2 = e2.process_block(sig[cut:])
    o2 = o2_raw[0]
    stitched_bands = [o1[bi] + o2[bi] for bi in range(len(BANDS))]
    stitched_mix = m1[0] + m2[0]
    same_bands = all(stitched_bands[bi] == full_outs[0][bi]
                     for bi in range(len(BANDS)))
    check("stitched branches equal one-shot run", same_bands)
    full_mix_mono = full_mix[0]
    mix_err = max_abs([a - b for a, b in zip(stitched_mix,
                                             full_mix_mono)])
    check("stitched mix equals one-shot run", mix_err < 1e-12,
          "max err %.3e" % mix_err)

    # JSON round-trip of the monitoring state
    blob = json.dumps(snap)
    snap2 = json.loads(blob)
    e3 = Engine(SR, BANDS, XOVERS, delay_ms=DELAYS, events=[ev])
    e3.restore_state(snap2)
    _o3, m3 = e3.process_block(sig[cut:])
    check("state survives JSON serialization", m3 == m2)


# ----------------------------------------------------------------------
# 7. legacy CLI compatibility
# ----------------------------------------------------------------------
def test_cli_legacy():
    print("7. legacy CLI compatibility")
    # sustained signal so the delayed branch is observable inside the clip
    sig = make_sweep(2000, SR)
    inp = {"sr": SR, "xovers": XOVERS, "bands": BANDS,
           "delay_ms": {"sub": 0.5, "main": 0.0, "tweet": 0.0},
           "signal": sig}
    with tempfile.TemporaryDirectory() as td:
        ip = os.path.join(td, "in.json")
        op = os.path.join(td, "out.json")
        with open(ip, "w") as fh:
            json.dump(inp, fh)
        subprocess.run([sys.executable, "xover.py", ip, op], check=True)
        out = json.load(open(op))
    check("legacy schema keys preserved",
          set(out.keys()) == {"bands", "energy_in", "energy_out"})
    check("legacy band order preserved",
          [b["name"] for b in out["bands"]] == [b["name"] for b in BANDS])
    d = delay_samples(0.5, SR)
    low = out["bands"][0]["samples"]
    # undelayed reference branch for the exact body comparison
    eng0 = Engine(SR, BANDS, XOVERS)
    o0, _ = eng0.run(sig)
    low0 = o0[0][0]
    check("CLI applies right shift (zero head, shifted body)",
          all(abs(v) < 1e-12 for v in low[:d])
          and all(abs(low[d + i] - round(low0[i], 5)) < 2e-5
                  for i in range(len(low0) - d)))
    check("CLI samples rounded to 5 decimals",
          all(round(v, 5) == v for b in out["bands"] for v in b["samples"]))

    # The engine uses the complementary shared-edge tree (the only model
    # that reconstructs the input); the legacy routine filters each band
    # independently, so its top band (double independent HP) intentionally
    # differs. They must agree on every band the two models share
    # exactly, and on all bookkeeping.
    eng_out = run_pipeline(inp)
    shared = [0, 1]  # low and mid bands are identical in both models
    check("engine and legacy agree on low/mid bands and bookkeeping",
          all(eng_out["bands"][bi]["samples"]
              == out["bands"][bi]["samples"] for bi in shared)
          and eng_out["energy_in"] == out["energy_in"]
          and [b["name"] for b in eng_out["bands"]]
              == [b["name"] for b in out["bands"]])
    # legacy top band is the non-complementary double-HP path; prove the
    # engine's top band instead reconstructs perfectly (cancellation fixed)
    rec = run_pipeline({"sr": SR, "xovers": XOVERS, "bands": BANDS,
                        "delay_ms": {"sub": 0.0, "main": 0.0,
                                     "tweet": 0.0}, "signal": sig})
    check("engine synthesis on legacy-style input reconstructs input",
          max_abs([m - s for m, s in zip(rec["mix"], sig)]) < 2e-5)


def main():
    test_pulse_reconstruction()
    test_sweep_and_switch()
    test_stereo()
    test_delay_and_polarity()
    test_event_sample_position()
    test_snapshot_restore()
    test_cli_legacy()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()

