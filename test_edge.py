#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""边界条件与确定性测试。"""
import json
import math
import os
import random
import struct
import subprocess
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
T = os.path.join(HERE, "data", "edge")
os.makedirs(T, exist_ok=True)
FS = 16000


def write_wav(path, samples, nch=1, sw=2, rate=FS):
    with wave.open(path, "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(sw)
        w.setframerate(rate)
        if sw == 2:
            pcm = [int(max(-32767, min(32767, v))) for v in samples]
            if nch == 2:
                stereo = []
                for v in pcm:
                    stereo.extend([v, v])
                pcm = stereo
            w.writeframes(struct.pack("<%dh" % len(pcm), *pcm))


def run(pcm, features=None, debug=False, block=2000):
    req = {"pcm": pcm, "fs": FS,
           "features": features or {"level_db": -45, "flat": 0.6, "min_ms": 200}}
    req["features"]["block_ms"] = block
    if debug:
        req["debug"] = True
    rp = os.path.join(T, pcm + ".req.json")
    op = os.path.join(T, pcm + ".out.json")
    json.dump(req, open(rp, "w"))
    subprocess.check_call([sys.executable, os.path.join(HERE, "cut.py"), rp, op])
    return json.load(open(op))


def main():
    rng = random.Random(1)
    # 1) 全静音 3s(长度非帧整数倍: 48123 样本)
    n = 48123
    write_wav(os.path.join(T, "silence.wav"),
              [rng.randint(-3, 3) for _ in range(n)])
    r = run("silence.wav")
    assert r["keep"] == [], "silence should have no keep, got %s" % r["keep"]
    assert len(r["drop"]) == 1 and r["drop"][0]["end"] >= n / FS - 0.02
    print("1 silence-only: keep=%d drop=%d OK" % (len(r["keep"]), len(r["drop"])))

    # 2) 极短音频(100ms, 5 帧)
    write_wav(os.path.join(T, "short.wav"),
              [int(8000 * math.sin(2 * math.pi * 200 * i / FS)) for i in range(1600)])
    r = run("short.wav")
    print("2 short 100ms: keep=%s drop=%s OK(不崩溃)" % (r["keep"], r["drop"]))

    # 3) 全内容(持续语音 2s), 首尾保护不越界
    sp = [int(6000 * math.sin(2 * math.pi * 180 * i / FS)
              * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / FS))) for i in range(2 * FS)]
    write_wav(os.path.join(T, "allspeech.wav"), sp)
    r = run("allspeech.wav")
    assert len(r["keep"]) == 1
    assert r["keep"][0]["start"] == 0.0
    assert abs(r["keep"][0]["end"] - 2.0) < 0.02
    print("3 all-speech: keep=%s OK(无负时间/越界)" % r["keep"])

    # 4) 立体声拒绝
    write_wav(os.path.join(T, "stereo.wav"), sp[:1600], nch=2)
    req = {"pcm": "stereo.wav", "features": {}}
    json.dump(req, open(os.path.join(T, "stereo.wav.req.json"), "w"))
    p = subprocess.run([sys.executable, os.path.join(HERE, "cut.py"),
                        os.path.join(T, "stereo.wav.req.json"),
                        os.path.join(T, "stereo.out.json")],
                       capture_output=True, text=True)
    assert p.returncode != 0 and "单声道" in p.stderr
    print("4 stereo rejected OK")

    # 5) 块大小确定性: 极不规则块(质数个样本)结果一致
    r1 = run("allspeech.wav", block=37)
    r2 = run("allspeech.wav", block=999)
    assert r1["boundaries"] == r2["boundaries"] and r1["keep"] == r2["keep"]
    print("5 block determinism (37ms vs 999ms) OK")

    # 6) debug 输出逐帧特征
    r = run("allspeech.wav", debug=True)
    assert "frames" in r and "frame_labels" in r and len(r["frames"]) == 100
    f0 = r["frames"][0]
    assert set(["frame", "t_ms", "rms_db", "peak_db", "flat", "zcr", "crest"]).issubset(f0)
    print("6 debug frames=%d features=%s OK" % (len(r["frames"]), sorted(f0)))

    # 7) 同一输入两次运行结果完全一致(确定性)
    a = run("silence.wav", block=123)
    b = run("silence.wav", block=123)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    print("7 deterministic rerun OK")
    print("ALL EDGE TESTS PASS")


if __name__ == "__main__":
    main()
