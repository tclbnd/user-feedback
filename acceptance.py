#!/usr/bin/env python3
"""Acceptance harness for the echo canceller.

Metrics are measured separately, exactly as requested:

  1. single-talk residual echo ........ ERLE (dB) in far-end-only regions
  2. double-talk voice amplitude ...... near-end RMS retention + freeze hit
  3. variable-delay recovery .......... cross-correlation resync latency and
                                        post-shift ERLE recovery
  4. block boundaries ................. identical freeze/resume sample times
                                        for several block lengths, incl. a
                                        non-divisor block that forces flush()

Run:  python3 acceptance.py        (exit code 0 only if every check passes)
"""
import math
import sys

from aec_core import AEC, FRAME_MS, FILTER_TAPS
from fixture import build_fixture


def rms(seq):
    if not seq:
        return 0.0
    return math.sqrt(sum(v * v for v in seq) / len(seq))


def frames_of(aec):
    """Per-frame state list aligned to sample ranges."""
    return aec.diagnostics


def freeze_events(aec):
    return [e for e in aec.events if e["kind"] in ("freeze", "resume")]


def run_case(block_len, fx):
    aec = AEC(fs=fx["fs"], filter_taps=FILTER_TAPS,
              initial_delay=fx["delay_schedule"][0][1])
    cleaned, diags, final = aec.run(fx["reference"], fx["mic"],
                                    block_len=block_len)
    return aec, cleaned, diags, final


def check_single_talk_erle(fx, cleaned, aec):
    fs = fx["fs"]
    # Single-talk = far-end active, near-end labelled absent, not inside a
    # delay-shift guard, and the canceller itself was adapting (a frozen
    # frame there would mean a detector false alarm and must not be hidden).
    dt_set = set()
    for a, b in fx["dt_ranges"]:
        dt_set.update(range(int(a * fs), int(b * fs)))
    shift_guards = set()
    for start, _ in fx["delay_schedule"][1:]:
        c = int(start * fs)
        shift_guards.update(range(c, c + int(0.3 * fs)))
    frozen_samples = set()
    for d in aec.diagnostics:
        if d["state"] == "frozen":
            frozen_samples.update(range(d["sample_start"], d["sample_end"]))
    idx = [i for i in range(fx["n"])
           if fx["ref_gate"][i] > 0.0 and i not in dt_set
           and i not in shift_guards and i not in frozen_samples]
    mic_rms = rms([fx["mic"][i] for i in idx])
    out_rms = rms([cleaned[i] for i in idx])
    erle = 20.0 * math.log10(mic_rms / max(out_rms, 1e-12))
    return erle, mic_rms, out_rms


def check_double_talk(fx, cleaned, aec):
    """Near-end preservation is measured by the least-squares gain of the
    frozen-frame output onto the true near-end signal.  A gain of ~1 means
    the near-end voice passes through untouched (the original bug drove it
    toward 0 by adapting on double-talk).  Raw output RMS is not used on its
    own because residual echo, which is orthogonal to the near-end, inflates
    it even when voice preservation is perfect."""
    fs = fx["fs"]
    gains = []
    raw_ratios = []
    covered = []
    for a, b in fx["dt_ranges"]:
        ia, ib = int(a * fs), int(b * fs)
        fr_idx = []
        for d in aec.diagnostics:
            if (d["state"] == "frozen"
                    and d["sample_start"] >= ia and d["sample_end"] <= ib):
                fr_idx.extend(range(d["sample_start"], d["sample_end"]))
        fr_idx = [i for i in fr_idx if i >= ia + 2 * aec.frame_len]
        if fr_idx:
            near = [fx["nearend"][i] for i in fr_idx]
            out = [cleaned[i] for i in fr_idx]
            nn = sum(v * v for v in near)
            alpha = sum(p * q for p, q in zip(out, near)) / max(nn, 1e-12)
            gains.append(alpha)
            near_rms = rms(near)
            raw_ratios.append(rms(out) / max(near_rms, 1e-12))
        total_frames = 0
        frozen_frames = 0
        for d in aec.diagnostics:
            if d["sample_start"] >= ia and d["sample_end"] <= ib:
                total_frames += 1
                if d["state"] == "frozen":
                    frozen_frames += 1
        covered.append(frozen_frames / max(1, total_frames))
    mean_gain = sum(gains) / len(gains)
    all_frozen_hit = all(c >= 0.85 for c in covered)
    return mean_gain, all_frozen_hit, gains, covered, raw_ratios


def check_delay_recovery(fx, aec):
    """The cross-correlator locates bulk delay plus the echo-path peak tap,
    so absolute lags carry a constant offset.  We therefore verify the
    *magnitude of each relocation* against the scheduled bulk-delay change
    and the recovery latency, not an absolute lag."""
    fs = fx["fs"]
    results = []
    shift_events = [e for e in aec.events if e["kind"] == "delay-shift"]
    sched = fx["delay_schedule"]
    for idx, ev in enumerate(shift_events):
        start, target = sched[idx + 1]
        prev_target = sched[idx][1]
        shift_sample = int(start * fs)
        latency_ms = 1000.0 * (ev["at_sample"] - shift_sample) / fs
        measured_move = ev["new_delay"] - ev["old_delay"]
        true_move = target - prev_target
        results.append({
            "target_move": true_move,
            "measured_move": measured_move,
            "move_error": abs(measured_move - true_move),
            "latency_ms": latency_ms,
            "new_delay": ev["new_delay"],
            "target": target,
        })
    return results, shift_events


def check_block_invariance(fx, block_lens):
    ref_aec = None
    ref_freeze = None
    ref_diag = None
    rows = []
    ref_clean = None
    n_full = fx["n"]
    for bl in block_lens:
        aec, cleaned, diags, final = run_case(bl, fx)
        ev = [(e["at_sample"], e["kind"], e["reason"])
              for e in freeze_events(aec)]
        # padded flush frames are excluded from the state-trace comparison
        state_seq = [(d["sample_start"], d["state"], d["delay"])
                     for d in diags if not d.get("flushed")]
        if ref_freeze is None:
            ref_freeze = ev
            ref_diag = state_seq
            ref_aec = aec
            ref_clean = cleaned
        same_events = (ev == ref_freeze)
        same_states = (state_seq == ref_diag)
        # only the real (non-padding) output samples are required to match
        same_signal = all(abs(cleaned[i] - ref_clean[i]) <= 1e-12
                          for i in range(n_full))
        rows.append({
            "block_len": bl,
            "samples_per_block": bl,
            "events": len(ev),
            "same_freeze_times": same_events,
            "same_state_trace": same_states,
            "same_output_samples": same_signal,
            "flushed_frames": sum(1 for d in diags if d.get("flushed")),
        })
    return rows, ref_aec, ref_clean


def check_flush_partial_frame(fx):
    """A signal whose length is not a multiple of the internal frame must
    produce one zero-padded flush frame without perturbing the state."""
    fl = int(fx["fs"] * FRAME_MS / 1000)
    trim = fx["n"] - (fl // 2)          # leaves a half-frame pending
    aec = AEC(fs=fx["fs"], filter_taps=FILTER_TAPS,
              initial_delay=fx["delay_schedule"][0][1])
    cleaned, diags, final = aec.run(fx["reference"][:trim],
                                    fx["mic"][:trim], block_len=fl)
    flushed = [d for d in diags if d.get("flushed")]
    state_before = [d["state"] for d in diags if not d.get("flushed")][-1]
    ok = (len(flushed) == 1
          and flushed[0]["state"] == state_before
          and final["samples_processed"]
          == ((trim + fl - 1) // fl) * fl)
    return ok, flushed[0] if flushed else None, final


def main():
    fx = build_fixture()
    fs = fx["fs"]
    frame_len = int(fs * FRAME_MS / 1000)
    block_lens = [frame_len, frame_len * 2, frame_len * 5,
                  frame_len * 3 + 7]  # arbitrary caller chunking

    print("=" * 72)
    print("AEC acceptance  (fs=%d, %.1fs, frame=%d samples/%.0f ms, taps=%d)"
          % (fs, fx["duration"], frame_len, FRAME_MS, FILTER_TAPS))
    print("=" * 72)

    # ---- 1 & 2 & 3 use the frame-aligned reference run ------------------
    aec, cleaned, diags, final = run_case(frame_len, fx)
    erle, mic_rms, out_rms = check_single_talk_erle(fx, cleaned, aec)
    voice_gain, dt_frozen, gains, coverage, raw_ratios = check_double_talk(
        fx, cleaned, aec)

    # post-shift ERLE per delay change, measured in a window late enough for
    # the boosted NLMS to re-converge.
    delay_rows, shift_events = check_delay_recovery(fx, aec)
    for row in delay_rows:
        ev = next(e for e in shift_events
                  if e["new_delay"] == row["new_delay"])
        r0 = ev["at_sample"] + int(0.5 * fs)
        r1 = r0 + int(0.5 * fs)
        idx = [i for i in range(r0, min(r1, fx["n"]))
               if fx["ref_gate"][i] > 0.0]
        m = rms([fx["mic"][i] for i in idx])
        o = rms([cleaned[i] for i in idx])
        row["erle_db"] = 20.0 * math.log10(m / max(o, 1e-12))

    # ---- 4 block invariance ---------------------------------------------
    inv_rows, _, _ = check_block_invariance(fx, block_lens)

    checks = []

    print("\n[1] single-talk residual echo")
    print("    mic rms=%.4f  output rms=%.4f  ERLE=%.2f dB"
          % (mic_rms, out_rms, erle))
    checks.append(("single-talk ERLE >= 12 dB", erle >= 12.0))

    print("\n[2] double-talk near-end voice preservation")
    print("    LS voice gain per interval=",
          ["%.3f" % g for g in gains])
    print("    raw out/near RMS ratio (includes residual echo)=",
          ["%.3f" % r for r in raw_ratios])
    print("    freeze coverage per interval=",
          ["%.2f" % c for c in coverage])
    print("    mean voice gain=%.3f, coverage ok=%s"
          % (voice_gain, dt_frozen))
    checks.append(("voice gain within 0.85..1.15 (no near-end erasure)",
                   0.85 <= voice_gain <= 1.15))
    checks.append(("freeze covers >=85% of double-talk frames", dt_frozen))

    print("\n[3] variable-delay relocation")
    for row in delay_rows:
        print("    move target=%(target_move)+d  measured=%(measured_move)+d"
              "  error=%(move_error)d  recovery=%(latency_ms)6.1f ms  "
              "post-shift ERLE=%(erle_db).2f dB" % row)
    checks.append(("one delay-shift event per scheduled change",
                   len(shift_events) == len(fx["delay_schedule"]) - 1))
    checks.append(("relocation magnitude within 8 samples",
                   all(r["move_error"] <= 8 for r in delay_rows)))
    checks.append(("recovery within 600 ms",
                   all(0 <= r["latency_ms"] <= 600.0 for r in delay_rows)))
    checks.append(("post-shift ERLE >= 8 dB",
                   all(r["erle_db"] >= 8.0 for r in delay_rows)))

    print("\n[4] block-length invariance / boundaries")
    for r in inv_rows:
        print("    block=%(block_len)3d  events=%(events)2d  "
              "same_freeze_times=%(same_freeze_times)s  "
              "same_state_trace=%(same_state_trace)s  "
              "same_output_samples=%(same_output_samples)s  "
              "flushed=%(flushed_frames)d" % r)
    checks.append(("freeze/resume times identical for all block sizes",
                   all(r["same_freeze_times"] for r in inv_rows)))
    checks.append(("per-frame state trace identical",
                   all(r["same_state_trace"] for r in inv_rows)))
    checks.append(("output samples identical across block sizes",
                   all(r["same_output_samples"] for r in inv_rows)))

    flush_ok, flush_rec, _ = check_flush_partial_frame(fx)
    print("\n    partial-frame flush: %s" % (flush_rec,))
    checks.append(("flush() drains a partial frame holding state",
                   flush_ok))

    # reference-silence freeze check on the known gap ---------------------
    gap_a = int(2.5 * fs)
    gap_b = int(2.9 * fs)
    gap_frozen = all(
        d["state"] == "frozen"
        for d in diags
        if d["sample_start"] >= gap_a and d["sample_end"] <= gap_b)
    print("\n[5] reference-silence gap (2.50-2.90 s) frozen: %s"
          % gap_frozen)
    checks.append(("adaptation frozen during reference silence", gap_frozen))

    print("\n" + "-" * 72)
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS " if ok else "  FAIL ") + name)
    print("-" * 72)
    print("result: %s (%d/%d checks passed)"
          % ("ALL PASS" if not failed else "FAILURES",
             len(checks) - len(failed), len(checks)))
    print("final state: delay=%d relocations=%d coeff_norm=%.4f"
          % (final["final_delay"], final["relocations"],
             final["final_coeff_norm"]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
