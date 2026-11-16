#!/usr/bin/env python3
"""Acceptance checks for dtmf.py: synthetic pairs, gaps, singles, speech.

Run:  python3 acceptance.py
"""

import json
import math
import os
import random
import sys
import tempfile

import dtmf

ROWS = {dtmf.KEYS[ri][ci]: r
        for ri, r in enumerate(dtmf.ROWS) for ci in range(4)}
COLS = {dtmf.KEYS[ri][ci]: c
        for ci, c in enumerate(dtmf.COLS) for ri in range(4)}
RESULTS = []


def check(name, cond, detail=""):
    cond = bool(cond)
    RESULTS.append((name, cond))
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    return cond


def freqs(key):
    return ROWS[key], COLS[key]


def add_tone(buf, sr, key, start_s, dur_s, amp=9000.0, row_gain=1.0,
             col_gain=1.0, phase=0.4):
    fr, fc = freqs(key)
    a = int(round(start_s * sr))
    b = int(round((start_s + dur_s) * sr))
    for i in range(max(0, a), min(len(buf), b)):
        t = (i - a) / float(sr)
        buf[i] += amp * (row_gain * math.sin(2 * math.pi * fr * t + phase)
                         + col_gain * math.sin(2 * math.pi * fc * t + 2 * phase))


def add_sine(buf, sr, freq, start_s, dur_s, amp):
    a = int(round(start_s * sr))
    b = int(round((start_s + dur_s) * sr))
    for i in range(max(0, a), min(len(buf), b)):
        buf[i] += amp * math.sin(2 * math.pi * freq * (i - a) / float(sr))


def speechlike(n, sr, seed=1, amp=2500.0):
    """Voiced harmonics with formants deliberately near DTMF rows/columns."""
    rnd = random.Random(seed)
    formants = ((700.0, 110.0), (1200.0, 130.0), (2600.0, 250.0))
    out = [0.0] * n
    phase = 0.0
    for i in range(n):
        t = i / float(sr)
        f0 = 118.0 * (1.0 + 0.06 * math.sin(2 * math.pi * 3.1 * t))
        phase += 2 * math.pi * f0 / sr
        v = 0.0
        k = 1
        while k * f0 < 0.45 * sr:
            fk = k * f0
            w = sum(math.exp(-((fk - f) / bw) ** 2) for f, bw in formants)
            v += (0.15 + w) * math.sin(k * phase + rnd.uniform(-0.05, 0.05))
            k += 1
        env = max(0.0, math.sin(2 * math.pi * 2.3 * t)) ** 1.5
        out[i] = amp * env * v / 4.0
    return out


def pair_energy(seg, sr, key):
    fr, fc = freqs(key)
    return min(dtmf.goertzel(seg, fr, sr), dtmf.goertzel(seg, fc, sr))


def max_step(seg):
    return max((abs(seg[i + 1] - seg[i]) for i in range(len(seg) - 1)),
               default=0.0)


def fade_limit(fade):
    """Largest per-sample gain change a fade-sample half cosine can produce."""
    return math.pi / (2.0 * fade) + 1e-9 if fade > 0 else 1e-9


def _diff(a, b):
    out = []
    for x, y in zip(a, b):
        if x != y:
            out.append("%s != %s" % (x, y))
    return "; ".join(out[:4])


def run(name, left, right, sr, tone_ms=40, threshold=1000.0, fade=80,
        expect=(), mute=(), keep=(), ints=True, extra_checks=None,
        tolerate=None):
    """Detect, then verify the invariants every output must satisfy."""
    conv = (lambda x: [int(round(v)) for v in x]) if ints else list
    l, r = conv(left), conv(right)
    res = dtmf.detect([l, r], sr, tone_ms, threshold, fade)
    dbg = dtmf.detect_debug([l, r], sr, tone_ms, threshold, fade)
    ok = True

    ok &= check(name + " | length preserved",
                len(res["left"]) == len(l) and len(res["right"]) == len(r),
                "%d/%d -> %d/%d" % (len(l), len(r), len(res["left"]),
                                    len(res["right"])))
    ok &= check(name + " | no output key pollution",
                set(res) == {"events", "left", "right", "twist"})

    ev = res["events"]
    tol = max(2, int(round((tolerate or 0.006) * sr)))
    ok &= check(name + " | event count", len(ev) == len(expect),
                "got %s" % ([(e["key"], e["start"], e["end"]) for e in ev],))
    ok &= check(name + " | events sorted",
                all(ev[i]["start"] <= ev[i + 1]["start"]
                    for i in range(len(ev) - 1)))
    for e, (key, s, en) in zip(ev, expect):
        ok &= check(name + " | %s span [%d,%d)" % (key, s, en),
                    e["key"] == key and abs(e["start"] - s) <= tol
                    and abs(e["end"] - en) <= tol,
                    "got [%d,%d) tol=%d" % (e["start"], e["end"], tol))

    # per-channel spans: mute must cover exactly the channel's own tone
    for e in dbg["events"]:
        ls, le = e["left_span"]
        rs, re_ = e["right_span"]
        ok &= check(name + " | %s event is union of channel spans" % e["key"],
                    e["start"] == min(ls, rs) and e["end"] == max(le, re_))
    for e in dbg["events"]:
        for ch, span in (("left", e["left_span"]), ("right", e["right_span"])):
            gain = dbg[ch + "_gain"]
            s, en = span
            ok &= check(name + " | %s %s span muted" % (ch, e["key"]),
                        en > s and all(gain[i] == 0.0 for i in range(s, en)))

    # explicit expected mute windows (caller supplied)
    for ch, spans, key in mute:
        buf = res[ch]
        for s, e in spans:
            ok &= check(name + " | %s muted [%d,%d)" % (ch, s, e),
                        all(buf[i] == 0 for i in range(max(0, s),
                                                       min(len(buf), e))))
            ok &= check(name + " | %s %s energy removed" % (ch, key),
                        pair_energy(buf[max(0, s):min(len(buf), e)], sr,
                                    key) < 1e-3)

    # untouched outside every per-channel span + fade
    busy = []
    for e in dbg["events"]:
        for span in (e["left_span"], e["right_span"]):
            busy.append((span[0] - fade - 1, span[1] + fade + 1))
    bad = 0
    for ch, ref in (("left", l), ("right", r)):
        for i in range(len(ref)):
            if any(a <= i <= b for a, b in busy):
                continue
            if ref[i] != res[ch][i]:
                bad += 1
    ok &= check(name + " | samples outside events preserved", bad == 0,
                "changed=%d" % bad)

    for ch, a, b in keep:
        ref = l if ch == "left" else r
        ok &= check(name + " | %s kept [%d,%d)" % (ch, a, b),
                    all(ref[i] == res[ch][i]
                        for i in range(max(0, a), min(len(ref), b))))

    # the gain path itself must be continuous (no audible step anywhere)
    max_gain_step = fade_limit(fade) if fade else 1.0
    for ch in ("left", "right"):
        gain = dbg[ch + "_gain"]
        steps = [abs(gain[i + 1] - gain[i]) for i in range(len(gain) - 1)]
        worst = max(steps) if steps else 0.0
        ok &= check(name + " | %s gain continuous" % ch,
                    worst <= max_gain_step,
                    "maxstep=%.5f limit=%.5f" % (worst, max_gain_step))
        ok &= check(name + " | %s gain in [0,1]" % ch,
                    all(0.0 <= g <= 1.0 for g in gain))

    if fade:
        for e in dbg["events"]:
            for ch in ("left", "right"):
                buf = res[ch]
                gain = dbg[ch + "_gain"]
                # every zero-gain sample equals input * 0; ends are zero
                span = e["left_span"] if ch == "left" else e["right_span"]
                ok &= check(name + " | %s zero at %s span ends" % (ch,
                                                                   e["key"]),
                            buf[span[0]] == 0 and buf[span[1] - 1] == 0)
                # monotonicity is only meaningful on a shoulder that no other
                # event's shoulder overlaps; overlapping ramps are still
                # continuous and can only be quieter than a single ramp
                shoulders = []
                for other in dbg["events"]:
                    osp = (other["left_span"] if ch == "left"
                           else other["right_span"])
                    shoulders.append((osp[0] - fade, osp[1] + fade))

                def clean(lo, hi):
                    return not any(lo < b and a < hi
                                   for a, b in shoulders
                                   if (a, b) != (span[0] - fade,
                                                 span[1] + fade))

                a = span[0] - fade
                if a >= 0 and clean(a, span[0]):
                    mono = all(gain[i] >= gain[i + 1] - 1e-12
                               for i in range(a, span[0] - 1))
                    ok &= check(name + " | %s down-ramp monotone" % ch, mono,
                                " " + " ".join("%.4f" % gain[i]
                                               for i in range(a, span[0])))
                b = span[1] + fade
                if b <= len(gain) and clean(span[1], b):
                    mono = all(gain[i] <= gain[i + 1] + 1e-12
                               for i in range(span[1], b - 1))
                    ok &= check(name + " | %s up-ramp monotone" % ch, mono,
                                " " + " ".join("%.4f" % gain[i]
                                               for i in range(span[1], b)))

    if extra_checks:
        ok &= extra_checks(res, dbg)
    return res, ok


def main():
    all_ok = True
    sr = 8000
    N = int(1.2 * sr)

    # ------------------------------------------------ basic pair, both sides
    l = [0.0] * N
    add_tone(l, sr, "5", 0.30, 0.080)
    res, ok = run("basic", l, list(l), sr, expect=[
        ("5", int(0.30 * sr), int(0.38 * sr))],
        mute=[("left", [(int(0.30 * sr), int(0.38 * sr))], "5"),
              ("right", [(int(0.30 * sr), int(0.38 * sr))], "5")])
    all_ok &= ok
    print("     confidence=%.3f twist=%s" % (res["events"][0]["confidence"],
                                             res["twist"]))

    # ----------------------------------------------------- channel offsets
    lo, ro = [0.0] * N, [0.0] * N
    add_tone(lo, sr, "7", 0.30, 0.080, amp=6000)
    add_tone(ro, sr, "7", 0.32, 0.080, amp=6000)
    add_sine(ro, sr, 300, 0.280, 0.020, 400)   # marker only on right
    add_sine(lo, sr, 300, 0.420, 0.020, 400)   # marker only on left
    lo_i = [int(round(v)) for v in lo]
    ro_i = [int(round(v)) for v in ro]
    res, ok = run("offset 20ms", lo, ro, sr, expect=[
        ("7", int(0.30 * sr), int(0.40 * sr))],
        mute=[("left", [(int(0.30 * sr) + 2, int(0.38 * sr) - 2)], "7"),
              ("right", [(int(0.32 * sr) + 2, int(0.40 * sr) - 2)], "7")],
        keep=[("right", int(0.282 * sr), int(0.298 * sr)),
              ("left", int(0.422 * sr), int(0.438 * sr))])
    all_ok &= ok
    # each channel's own mute span must be its own tone, not the union
    if res["events"]:
        ev = res["events"][0]
        left_changed = [i for i, (a, b) in enumerate(zip(lo_i, res["left"]))
                        if a != b]
        right_changed = [i for i, (a, b) in enumerate(zip(ro_i, res["right"]))
                         if a != b]
        all_ok &= check("offset 20ms | left span follows left tone",
                        left_changed and left_changed[0] <= int(0.30 * sr) + 1
                        and left_changed[-1] <= int(0.38 * sr) + 2 * 80)
        all_ok &= check("offset 20ms | right span follows right tone",
                        right_changed
                        and right_changed[0] >= int(0.32 * sr) - 1 - 2 * 80
                        and right_changed[-1] >= int(0.40 * sr) - 1)
        all_ok &= check("offset 20ms | event union", ev["start"]
                        <= int(0.30 * sr) + 1 and ev["end"]
                        >= int(0.40 * sr) - 1,
                        " %s" % (ev,))
        # the right channel must stay untouched while only the left tone is
        # sounding (0.30..0.32 s) and vice versa (0.40..0.42 s)
        all_ok &= check("offset 20ms | right untouched during left-only part",
                        all(ro_i[i] == res["right"][i]
                            for i in range(int(0.30 * sr) + 2 * 80,
                                           int(0.32 * sr) - 1)))
        all_ok &= check("offset 20ms | left untouched during right-only part",
                        all(lo_i[i] == res["left"][i]
                            for i in range(int(0.38 * sr) + 2 * 80,
                                           int(0.40 * sr) - 1)))
        print("     left changed %s..%s right changed %s..%s" % (
            left_changed[0] if left_changed else None,
            left_changed[-1] if left_changed else None,
            right_changed[0] if right_changed else None,
            right_changed[-1] if right_changed else None))

    lo, ro = [0.0] * N, [0.0] * N
    add_tone(lo, sr, "3", 0.30, 0.080)
    add_tone(ro, sr, "3", 0.34, 0.080)          # exactly tone_ms later
    res, ok = run("offset 40ms = tone_ms", lo, ro, sr, expect=[
        ("3", int(0.30 * sr), int(0.42 * sr))])
    all_ok &= ok

    lo, ro = [0.0] * N, [0.0] * N
    add_tone(lo, sr, "3", 0.30, 0.080)
    add_tone(ro, sr, "3", 0.42, 0.080)          # beyond allowed skew
    res, ok = run("offset > tone_ms rejected", lo, ro, sr, expect=[])
    all_ok &= ok

    # key must match on both channels
    lo, ro = [0.0] * N, [0.0] * N
    add_tone(lo, sr, "1", 0.30, 0.080)
    add_tone(ro, sr, "2", 0.30, 0.080)
    res, ok = run("different keys per channel rejected", lo, ro, sr, expect=[])
    all_ok &= ok

    # ------------------------------------------------------------ gaps
    l2 = [0.0] * int(2.4 * sr)
    add_tone(l2, sr, "5", 0.25, 0.080)
    add_tone(l2, sr, "5", 0.35, 0.080)          # 20 ms gap < one window
    res, ok = run("short same-key gap merged", l2, list(l2), sr, expect=[
        ("5", int(0.25 * sr), int(0.43 * sr))])
    all_ok &= ok

    l3 = [0.0] * int(2.4 * sr)
    add_tone(l3, sr, "5", 0.25, 0.080)
    add_tone(l3, sr, "5", 0.73, 0.080)          # long gap -> two events
    res, ok = run("long same-key gap split", l3, list(l3), sr, expect=[
        ("5", int(0.25 * sr), int(0.33 * sr)),
        ("5", int(0.73 * sr), int(0.81 * sr))])
    all_ok &= ok

    l4 = [0.0] * N
    add_tone(l4, sr, "4", 0.20, 0.080)
    add_tone(l4, sr, "6", 0.38, 0.080)
    res, ok = run("different keys with gap", l4, list(l4), sr, expect=[
        ("4", int(0.20 * sr), int(0.28 * sr)),
        ("6", int(0.38 * sr), int(0.46 * sr))])
    all_ok &= ok

    l5 = [0.0] * N
    add_tone(l5, sr, "4", 0.20, 0.080)
    add_tone(l5, sr, "9", 0.28, 0.080)          # back-to-back
    res, ok = run("back-to-back keys", l5, list(l5), sr, expect=[
        ("4", int(0.20 * sr), int(0.28 * sr)),
        ("9", int(0.28 * sr), int(0.36 * sr))])
    all_ok &= ok

    # -------------------------------------------------- minimum duration
    for ms in (10, 20, 30, 40, 60, 80):
        b = [0.0] * int(0.8 * sr)
        add_tone(b, sr, "8", 0.30, ms / 1000.0)
        exp = [("8", int(0.30 * sr), int((0.30 + ms / 1000.0) * sr))
               ] if ms >= 40 else []
        res, ok = run("tone %dms vs tone_ms=40" % ms, b, list(b), sr,
                      expect=exp)
        all_ok &= ok
        if exp and res["events"]:
            e = res["events"][0]
            print("     %d ms tone -> %.1f ms span (samples %d..%d)" % (
                ms, 1000.0 * (e["end"] - e["start"]) / sr, e["start"], e["end"]))

    # --------------------------------------------------- single frequency
    for f in (697, 770, 852, 941, 1209, 1336, 1477, 1633, 900):
        b = [0.0] * N
        add_sine(b, sr, f, 0.30, 0.20, 9000)
        res, ok = run("single %d Hz" % f, b, list(b), sr, expect=[])
        all_ok &= ok
    b = [0.0] * N
    for f in (697, 770, 1209):
        add_sine(b, sr, f, 0.30, 0.20, 6000)
    res, ok = run("three row tones", b, list(b), sr, expect=[])
    all_ok &= ok

    b = [0.0] * N
    add_tone(b, sr, "6", 0.30, 0.080, col_gain=0.002)
    res, ok = run("row only (col 60 dB down)", b, list(b), sr, expect=[])
    all_ok &= ok
    b = [0.0] * N
    add_tone(b, sr, "6", 0.30, 0.080, row_gain=0.002)
    res, ok = run("col only (row 54 dB down)", b, list(b), sr, expect=[])
    all_ok &= ok

    # ------------------------------------------------------ speech cases
    vsr = 16000
    nn = int(2.0 * vsr)
    sp = speechlike(nn, vsr, seed=7)
    l6, r6 = list(sp), list(sp)
    add_tone(l6, vsr, "2", 0.90, 0.080)
    add_tone(r6, vsr, "2", 0.90, 0.080)
    res, ok = run("speech + tone", l6, r6, vsr, ints=False,
                  expect=[("2", int(0.90 * vsr), int(0.98 * vsr))],
                  mute=[("left", [(int(0.90 * vsr), int(0.98 * vsr))], "2")],
                  keep=[("left", int(0.86 * vsr), int(0.89 * vsr)),
                        ("left", int(0.99 * vsr), int(1.05 * vsr))])
    all_ok &= ok

    sp2 = speechlike(nn, vsr, seed=11, amp=4000.0)
    res, ok = run("speech only (formants on DTMF rows)", sp2, list(sp2), vsr,
                  ints=False, expect=[])
    all_ok &= ok

    l7, r7 = list(sp2), list(sp2)
    add_tone(l7, vsr, "A", 1.20, 0.100, amp=7000)
    add_tone(r7, vsr, "A", 1.22, 0.100, amp=7000)
    res, ok = run("speech + delayed tone", l7, r7, vsr, ints=False,
                  expect=[("A", int(1.20 * vsr), int(1.32 * vsr))])
    all_ok &= ok

    # ------------------------------------------- rates, windows, chunking
    for msr in (8000, 11025, 16000, 22050, 44100):
        b = [0.0] * int(0.6 * msr)
        add_tone(b, msr, "D", 0.15, 0.060)
        res, ok = run("rate %d" % msr, b, list(b), msr, tone_ms=40,
                      fade=int(0.010 * msr),
                      expect=[("D", int(0.15 * msr), int(0.21 * msr))])
        all_ok &= ok

    for tms in (20, 30, 40, 60, 100):
        b = [0.0] * int(0.8 * sr)
        add_tone(b, sr, "0", 0.20, 1.5 * tms / 1000.0)
        res, ok = run("window %d ms" % tms, b, list(b), sr, tone_ms=tms,
                      expect=[("0", int(0.20 * sr),
                               int((0.20 + 1.5 * tms / 1000.0) * sr))])
        all_ok &= ok

    # tone starting at odd phases / non hop-aligned offsets
    for off in (0, 1, 37, 79, 123, 999):
        b = [0.0] * int(0.8 * sr)
        s = (0.25 * sr + off) / sr
        add_tone(b, sr, "C", s, 0.070)
        res, ok = run("unaligned start +%d" % off, b, list(b), sr, expect=[
            ("C", int(round(s * sr)), int(round(s * sr)) + int(0.070 * sr))])
        all_ok &= ok

    for fade in (20, 200):
        b = [0.0] * int(0.8 * sr)
        add_tone(b, sr, "1", 0.30, 0.08)
        res, ok = run("fade %d" % fade, b, list(b), sr, fade=fade,
                      expect=[("1", int(0.30 * sr), int(0.38 * sr))])
        all_ok &= ok
        if fade and res["events"]:
            e = res["events"][0]
            pk = max(abs(v) for v in res["left"])
            gains = [abs(res["left"][e["start"] - fade + k])
                     / float(max(1, pk)) for k in range(fade)]
            mono = all(gains[k] <= gains[k + 1] + 1e-6 for k in range(fade - 1))
            ok = check("fade %d | down-ramp monotone from 0" % fade,
                       mono and gains[0] < 0.05)
            all_ok &= ok

    # fade = 0 is an explicit hard cut: allowed, but must still be exact
    b = [0.0] * int(0.8 * sr)
    add_tone(b, sr, "1", 0.30, 0.08)
    bi = [int(round(v)) for v in b]
    res, ok = run("fade 0 (hard cut requested)", b, list(b), sr, fade=0,
                  expect=[("1", int(0.30 * sr), int(0.38 * sr))])
    all_ok &= ok
    if res["events"]:
        e = res["events"][0]
        all_ok &= check("fade 0 | hard cut at span ends",
                        res["left"][e["start"]] == 0
                        and res["left"][e["end"] - 1] == 0
                        and all(bi[i] == res["left"][i]
                                for i in range(max(0, e["start"] - 200),
                                               e["start"]))
                        and all(bi[i] == res["left"][i]
                                for i in range(e["end"], min(len(bi),
                                                             e["end"] + 200))))

    # ---------------------------------------------- twist / amplitude
    b = [0.0] * N
    add_tone(b, sr, "9", 0.30, 0.080, row_gain=1.0, col_gain=0.30)
    res, ok = run("twist +10 dB", b, list(b), sr, expect=[
        ("9", int(0.30 * sr), int(0.38 * sr))])
    all_ok &= ok
    print("     twist=%.2f (energy ratio; amplitude ratio^2=0.09 -> %.2f)" % (
        res["twist"].get("9", 0), 1 / 0.09))

    for amp in (400, 1500, 20000):
        b = [0.0] * N
        add_tone(b, sr, "B", 0.30, 0.080, amp=amp)
        res, ok = run("amplitude %d" % amp, b, list(b), sr, expect=[
            ("B", int(0.30 * sr), int(0.38 * sr))])
        all_ok &= ok

    # threshold above the signal energy (Goertzel floor is |X|^2 ~ (A*N/2)^2,
    # i.e. ~1e11 for these levels, so 1e18 is comfortably above it)
    b = [0.0] * N
    add_tone(b, sr, "D", 0.30, 0.080, amp=2000)
    res, ok = run("threshold too high", b, list(b), sr, threshold=1e18,
                  expect=[])
    all_ok &= ok

    # ------------------------------------------------- noise and gradients
    rnd = random.Random(3)
    b = [0.0] * int(1.4 * sr)
    add_tone(b, sr, "6", 0.40, 0.080, amp=5000)
    noisy = [v + rnd.gauss(0, 120) for v in b]
    res, ok = run("white noise -32 dB", noisy, list(noisy), sr, ints=False,
                  expect=[("6", int(0.40 * sr), int(0.48 * sr))])
    all_ok &= ok

    # slowly rising/falling tone amplitude (envelope within the tone)
    b = [0.0] * int(1.4 * sr)
    add_tone(b, sr, "8", 0.40, 0.100)
    for i in range(int(0.40 * sr), int(0.50 * sr)):
        t = (i - int(0.40 * sr)) / float(int(0.10 * sr))
        b[i] *= 0.35 + 0.65 * t
    res, ok = run("amplitude ramp inside tone", b, list(b), sr,
                  expect=[("8", int(0.40 * sr), int(0.50 * sr))])
    all_ok &= ok

    # clipped (square-ish) tone pair
    b = [0.0] * int(1.4 * sr)
    add_tone(b, sr, "A", 0.40, 0.080, amp=20000)
    b = [max(-9000.0, min(9000.0, v)) for v in b]
    res, ok = run("clipped tone", b, list(b), sr, ints=False,
                  expect=[("A", int(0.40 * sr), int(0.48 * sr))])
    all_ok &= ok

    # quiet tone adjacent to a loud speech burst (no overlap): the boundary
    # must not swallow the speech on either side
    b = speechlike(int(1.6 * sr), sr, seed=5, amp=9000.0)
    for i in range(int(0.40 * sr), int(0.44 * sr)):
        b[i] = 0.0
    for i in range(int(0.52 * sr), int(0.56 * sr)):
        b[i] = 0.0
    add_tone(b, sr, "3", 0.44, 0.080, amp=1500)
    res, ok = run("quiet tone between loud speech", b, list(b), sr,
                  ints=False, tolerate=0.010,
                  expect=[("3", int(0.44 * sr), int(0.52 * sr))])
    all_ok &= ok

    # tone overlapping a loud speech burst at a realistic SNR
    b = speechlike(int(1.6 * sr), sr, seed=5, amp=9000.0)
    add_tone(b, sr, "3", 0.45, 0.080, amp=6000)
    res, ok = run("tone over loud speech (realistic SNR)", b, list(b), sr,
                  ints=False, tolerate=0.010,
                  expect=[("3", int(0.45 * sr), int(0.53 * sr))])
    all_ok &= ok

    # ------------------------------------------ sequences and long input
    seq = [("1", 0.10), ("2", 0.30), ("3", 0.50), ("4", 0.70), ("5", 0.90),
           ("6", 1.10), ("7", 1.30), ("8", 1.50), ("9", 1.70), ("*", 1.90),
           ("0", 2.10), ("#", 2.30), ("A", 2.50), ("B", 2.70), ("C", 2.90),
           ("D", 3.10)]
    n_long = int(4.0 * sr)
    l = [0.0] * n_long
    r = [0.0] * n_long
    expect = []
    for i, (key, t) in enumerate(seq):
        skew = (i % 3) * 0.005
        add_tone(l, sr, key, t, 0.080)
        add_tone(r, sr, key, t + skew, 0.080)
        expect.append((key, int(round(t * sr)),
                       int(round((t + 0.08 + skew) * sr))))
    res, ok = run("16-key sequence", l, r, sr, expect=expect)
    all_ok &= ok
    all_ok &= check("16-key sequence | keys in order",
                    [e["key"] for e in res["events"]] == [k for k, _ in seq])
    all_ok &= check("16-key sequence | confidences positive",
                    all(e["confidence"] > 1.0 for e in res["events"]))
    all_ok &= check("16-key sequence | twist covers all keys",
                    set(res["twist"]) == {k for k, _ in seq})

    # block-processing equivalence: the same samples split into 1 s chunks
    # must give the same key/position (to within one frame of rounding at
    # block borders, where a window cannot see across the cut)
    block_events = []
    chunk = int(1.0 * sr)
    for a in range(0, n_long, chunk):
        bs, be = a, min(n_long, a + chunk)
        sub = dtmf.detect([l[bs:be], r[bs:be]], sr, 40, 1000.0, 80)
        for e in sub["events"]:
            block_events.append((e["key"], e["start"] + bs, e["end"] + bs))
    whole = [(e["key"], e["start"], e["end"]) for e in res["events"]]
    # A chunk that ends inside a tone cannot see that tone's release, so the
    # agreement is required to within one analysis window, not sample-exact.
    slack = int(0.040 * sr)
    ok_blocks = len(block_events) == len(whole)
    if ok_blocks:
        for (k1, s1, e1), (k2, s2, e2) in zip(block_events, whole):
            if k1 != k2 or abs(s1 - s2) > slack or abs(e1 - e2) > slack:
                ok_blocks = False
    all_ok &= check("16-key sequence | 1 s blocks agree", ok_blocks,
                    "%d vs %d %s" % (len(block_events), len(whole),
                                     _diff(block_events, whole)))

    # overlapping (simultaneous) different keys: DTMF cannot do this, but
    # the detector must not invent a third key from the sum
    b = [0.0] * int(1.0 * sr)
    add_tone(b, sr, "1", 0.20, 0.080)
    add_tone(b, sr, "D", 0.20, 0.080)
    res, ok = run("two simultaneous keys", b, list(b), sr, expect=[])
    all_ok &= ok

    # two different keys on different channels are not a pair
    l = [0.0] * int(1.0 * sr)
    r = [0.0] * int(1.0 * sr)
    add_tone(l, sr, "5", 0.20, 0.080)
    add_tone(r, sr, "5", 0.20, 0.080)
    add_tone(r, sr, "8", 0.40, 0.080)
    res, ok = run("extra tone on one channel only", l, r, sr,
                  expect=[("5", int(0.20 * sr), int(0.28 * sr))])
    all_ok &= ok
    r_i = [int(round(v)) for v in r]
    all_ok &= check("extra tone on one channel only | right still unmuted "
                    "over its extra tone",
                    all(r_i[i] == res["right"][i]
                        for i in range(int(0.40 * sr), int(0.48 * sr))))

    # ------------------------------------------------------- edge cases
    # empty and sub-window inputs
    for n in (0, 1, 5, 100):
        res = dtmf.detect([[], []], sr, 40, 1000.0, 80) if n == 0 else \
            dtmf.detect([[0] * n, [0] * n], sr, 40, 1000.0, 80)
        all_ok &= check("degenerate length %d" % n,
                        res["events"] == [] and len(res["left"]) == n
                        and len(res["right"]) == n)
    # single channel input (mono) must not crash and stays equal-length
    mono = [int(round(v)) for v in
            [9000 * math.sin(2 * math.pi * 697 * i / float(sr))
             for i in range(int(0.4 * sr))]]
    res = dtmf.detect([mono], sr, 40, 1000.0, 80)
    all_ok &= check("mono input handled",
                    len(res["left"]) == len(res["right"]) == len(mono)
                    and res["events"] == [])

    # tone flush against the start and the end of the file
    for pos, name in ((0.0, "tone at file start"),
                      (None, "tone at file end")):
        n = int(0.30 * sr)
        dur = int(0.080 * sr)
        a = 0 if pos is not None else n - dur
        b = [0.0] * n
        add_tone(b, sr, "7", a / float(sr), dur / float(sr))
        res, ok = run(name, b, list(b), sr, expect=[("7", a, a + dur)],
                      tolerate=0.012)
        all_ok &= ok

    # fade longer than the gap between two events must not break anything
    b = [0.0] * int(1.0 * sr)
    add_tone(b, sr, "1", 0.20, 0.080)
    add_tone(b, sr, "2", 0.29, 0.080)
    res, ok = run("fade spans the inter-event gap", b, list(b), sr,
                  fade=int(0.030 * sr), tolerate=0.008,
                  expect=[("1", int(0.20 * sr), int(0.28 * sr)),
                          ("2", int(0.29 * sr), int(0.37 * sr))])
    all_ok &= ok
    all_ok &= check("fade spans the inter-event gap | gain never negative",
                    all(0.0 <= g <= 1.0
                        for g in dtmf.detect_debug(
                            [[int(round(v)) for v in b],
                             [int(round(v)) for v in b]],
                            sr, 40, 1000.0, int(0.030 * sr))["left_gain"]))

    # ------------------------------------------- reported evidence fields
    b = [0.0] * N
    add_tone(b, sr, "5", 0.30, 0.080, amp=9000)
    res = dtmf.detect([b, b], sr, 40, 1000.0, 80)
    ev = res["events"][0]
    seg = b[int(0.30 * sr):int(0.38 * sr)]
    re = min(dtmf.goertzel(seg, 770.0, sr), dtmf.goertzel(seg, 852.0, sr))
    print("     confidence=%.3f  event=%s twist=%s" % (
        ev["confidence"], ev, res["twist"]))
    all_ok &= check("confidence is finite and >= 1 for a clear pair",
                    math.isfinite(ev["confidence"]) and ev["confidence"] > 1.0)
    all_ok &= check("confidence rounded to 3 decimals",
                    abs(ev["confidence"] * 1000
                        - round(ev["confidence"] * 1000)) < 1e-6)
    all_ok &= check("twist reported for every detected key",
                    set(res["twist"]) == {e["key"] for e in res["events"]})
    # distortion changes confidence monotonically (weaker pair -> lower)
    b2 = [0.0] * N
    add_tone(b2, sr, "5", 0.30, 0.080, amp=900)
    ev2 = dtmf.detect([b2, b2], sr, 40, 1000.0, 80)["events"][0]
    all_ok &= check("weaker pair gives lower confidence",
                    ev2["confidence"] < ev["confidence"],
                    "%.3f < %.3f" % (ev2["confidence"], ev["confidence"]))

    # ------------------------------------------------------- CLI, output
    tmp = tempfile.mkdtemp()
    inp, outp = os.path.join(tmp, "in.json"), os.path.join(tmp, "out.json")
    b = [0.0] * int(0.8 * sr)
    add_tone(b, sr, "B", 0.20, 0.08)
    bi = [int(round(v)) for v in b]
    with open(inp, "w") as fh:
        json.dump({"samples": [bi, bi], "sample_rate": sr, "tone_ms": 40,
                   "threshold": 1000.0, "fade": 80}, fh)
    rc = dtmf.main(["dtmf.py", inp, outp])
    out = json.load(open(outp))
    all_ok &= check("CLI exit 0", rc == 0)
    all_ok &= check("CLI output keys", set(out) == {"events", "left", "right",
                                                    "twist"})
    all_ok &= check("CLI lengths",
                    len(out["left"]) == len(bi) == len(out["right"]))
    all_ok &= check("CLI event", len(out["events"]) == 1
                    and out["events"][0]["key"] == "B"
                    and set(out["events"][0]) == {"key", "start", "end",
                                                  "confidence"})
    all_ok &= check("CLI keeps int type",
                    all(isinstance(v, int) for v in out["left"]))
    all_ok &= check("deterministic",
                    dtmf.detect([bi, bi], sr, 40, 1000.0, 80) ==
                    dtmf.detect([bi, bi], sr, 40, 1000.0, 80))

    failed = sum(1 for _, c in RESULTS if not c)
    print("\n%d checks, %d failed" % (len(RESULTS), failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
