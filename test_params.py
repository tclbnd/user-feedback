#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剪切策略参数行为测试: 瞬态删除、最小段长、保护带、近邻合并、淡入淡出。"""
import json
import math
import os
import random
import struct
import subprocess
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
T = os.path.join(HERE, "data", "param")
os.makedirs(T, exist_ok=True)
FS = 16000


def write_wav(path, samples):
    pcm = [int(max(-32767, min(32767, v))) for v in samples]
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(struct.pack("<%dh" % len(pcm), *pcm))


def run(pcm, features):
    req = {"pcm": pcm, "fs": FS, "features": features}
    rp = os.path.join(T, pcm + ".req.json")
    op = os.path.join(T, pcm + ".out.json")
    json.dump(req, open(rp, "w"))
    subprocess.check_call([sys.executable, os.path.join(HERE, "cut.py"), rp, op])
    return json.load(open(op))


def speech(n, level=-24):
    rng = random.Random(5)
    out = []
    for i in range(n):
        v = sum(g * math.sin(2 * math.pi * 150 * h * i / FS)
                for h, g in ((1, 1.0), (2, 0.5), (3, 0.25)))
        env = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(2 * math.pi * 3.2 * i / FS))
        out.append(v * env)
    g = 32767 * 10 ** (level / 20) / math.sqrt(sum(v * v for v in out) / n)
    return [v * g for v in out]


def main():
    rng = random.Random(9)
    # 场景: 1s 安静 + 单个 60ms 衰变咔哒 + 1s 安静
    x = [rng.randint(-50, 50) for _ in range(FS)]
    click_n = int(0.06 * FS)
    for k in range(click_n):
        x.append(12000 * math.exp(-k / 20.0) * (rng.random() * 2 - 1))
    x += [rng.randint(-50, 50) for _ in range(FS - click_n)]
    write_wav(os.path.join(T, "click.wav"), x)
    r = run("click.wav", {"level_db": -45, "flat": 0.6, "min_ms": 200})
    kept = sum(k["end"] - k["start"] for k in r["keep"])
    print("isolated click keep time=%.3fs (应≈0)" % kept)
    assert kept < 0.2, "isolated transient must be removed"

    # 两个语音段间隔 200ms (< merge_ms=300) 应合并为一段
    y = speech(int(1.0 * FS)) + [0] * int(0.2 * FS) + speech(int(1.0 * FS))
    write_wav(os.path.join(T, "merge.wav"), y)
    r = run("merge.wav", {"level_db": -45, "flat": 0.6, "min_ms": 200,
                          "merge_ms": 300, "guard_ms": 50})
    print("merge case keep segments=%d (应=1)" % len(r["keep"]))
    assert len(r["keep"]) == 1
    # 间隔 500ms (> merge_ms) 应保留为两段
    y2 = speech(int(1.0 * FS)) + [0] * int(0.5 * FS) + speech(int(1.0 * FS))
    write_wav(os.path.join(T, "nomerge.wav"), y2)
    r2 = run("nomerge.wav", {"level_db": -45, "flat": 0.6, "min_ms": 200,
                             "merge_ms": 300, "guard_ms": 50})
    print("no-merge case keep segments=%d (应=2)" % len(r2["keep"]))
    assert len(r2["keep"]) == 2

    # 保护带: 语音段两侧各扩 50ms(落在静音内)
    r3 = run("nomerge.wav", {"level_db": -45, "flat": 0.6, "min_ms": 200,
                             "merge_ms": 300, "guard_ms": 50})
    first = r3["keep"][0]
    print("guard: first start=%.3f (应≈0, 受边界钳制), end=%.3f (应≈1.05)"
          % (first["start"], first["end"]))
    assert abs(first["end"] - 1.05) < 0.03
    # 加大保护到 120ms
    r4 = run("nomerge.wav", {"level_db": -45, "flat": 0.6, "min_ms": 200,
                             "merge_ms": 300, "guard_ms": 120})
    print("guard120: first end=%.3f (应≈1.12)" % r4["keep"][0]["end"])
    assert abs(r4["keep"][0]["end"] - 1.12) < 0.03

    # 淡入淡出参数透传到 keep 段
    r5 = run("nomerge.wav", {"level_db": -45, "flat": 0.6, "min_ms": 200,
                             "guard_ms": 50, "fade_ms": 30})
    assert all(k["fade_in_ms"] == 30 and k["fade_out_ms"] == 30 for k in r5["keep"])
    print("fade param propagated=30ms OK")
    print("ALL PARAMETER TESTS PASS")


if __name__ == "__main__":
    main()
