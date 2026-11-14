#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯电平门限基线 vs 多特征方案 对照(同一批人工/期望标签)。"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FRAME_S = 0.02


def mask(intervals, n_frames):
    m = [0] * n_frames
    for it in intervals:
        a = int(round(it["start"] / FRAME_S))
        b = int(round(it["end"] / FRAME_S))
        for k in range(max(0, a), min(n_frames, b)):
            m[k] = 1
    return m


def metrics(out, human, duration):
    n = int(round(duration / FRAME_S))
    km, hm = mask(out["keep"], n), mask(human, n)
    sp = [i for i, h in enumerate(hm) if h]
    si = [i for i, h in enumerate(hm) if not h]
    fd = sum(1 for i in sp if km[i] == 0) / max(1, len(sp))
    fk = sum(1 for i in si if km[i] == 1) / max(1, len(si))
    return 100 * fd, 100 * fk


def main():
    expected = json.load(open(os.path.join(DATA, "expected.json")))
    expect_decision = {"ac": "drop", "keyboard": "drop", "quiet": "drop",
                       "speech": "keep", "whisper": "keep", "breath": "keep"}
    for name in ("main", "inject"):
        req_path = os.path.join(DATA, name + ".req.json")
        base_out = os.path.join(DATA, name + ".baseline.json")
        subprocess.check_call([sys.executable, os.path.join(HERE, "cut_baseline.py"),
                               req_path, base_out])
        base = json.load(open(base_out))
        req = json.load(open(req_path))
        wav_dur = {"main": 28.0, "inject": 12.0}[name]
        fd, fk = metrics(base, req["human"], wav_dur)
        # 逐段决策
        n = int(round(wav_dur / FRAME_S))
        km = mask(base["keep"], n)
        print("=" * 64)
        print("基线(纯电平门限) %s:" % name)
        print("  误删语音 %.2f%%  误留静音 %.2f%%" % (fd, fk))
        wrong = []
        for seg in expected[name]["segments"]:
            a = int(round(seg["start"] / FRAME_S))
            b = int(round(seg["end"] / FRAME_S))
            keep_ratio = sum(km[a:b]) / max(1, b - a)
            dec = "keep" if keep_ratio >= 0.5 else "drop"
            want = expect_decision[seg["kind"]]
            if dec != want:
                wrong.append((seg["kind"], seg["start"], seg["end"], dec, want))
        print("  决策不一致段: %d" % len(wrong))
        for wseg in wrong:
            print("    %-9s %.2f-%.2f 基线=%s 期望=%s" % wseg)

        smart = json.load(open(os.path.join(DATA, name + ".out.json")))
        fd2, fk2 = metrics(smart, req["human"], wav_dur)
        print("多特征方案 %s:" % name)
        print("  误删语音 %.2f%%  误留静音 %.2f%%  保留段数=%d"
              % (fd2, fk2, len(smart["keep"])))


if __name__ == "__main__":
    main()
