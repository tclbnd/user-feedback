#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合成验收样例(纯标准库):
  data/main.wav        28s 长录音综合场景: 语音/低语/空调底噪/键盘/气口/安静
  data/inject.wav      12s 注入专项: 空调底噪 + 键盘敲击 + 低语
同时写出 main.req.json / inject.req.json(含 human 人工剪辑参考)与期望标签 expected.json。

所有片段标签(ground truth)在此声明, evaluate.py 据此核对逐段决策。
"""
import json
import math
import os
import random
import struct
import wave

FS = 16000
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA, exist_ok=True)


# ---------------------------------------------------------------- 基础合成
class Synth:
    def __init__(self, seed=42):
        self.rng = random.Random(seed)

    def noise(self, n, amp=1.0):
        return [self.rng.uniform(-amp, amp) for _ in range(n)]

    def lp(self, x, alpha):
        acc = 0.0
        y = [0.0] * len(x)
        for i, v in enumerate(x):
            acc += alpha * (v - acc)
            y[i] = acc
        return y

    def hp(self, x, alpha):
        acc = 0.0
        y = [0.0] * len(x)
        for i, v in enumerate(x):
            acc += alpha * (v - acc)
            y[i] = v - acc
        return y

    def peak_eq(self, x, fc, q, gain_db):
        a = 10 ** (gain_db / 40.0)
        w0 = 2 * math.pi * fc / FS
        alpha = math.sin(w0) / (2 * q)
        cosw = math.cos(w0)
        b0 = 1 + alpha * a
        b1 = -2 * cosw
        b2 = 1 - alpha * a
        a0 = 1 + alpha / a
        a1 = -2 * cosw
        a2 = 1 - alpha / a
        x1 = x2 = y1 = y2 = 0.0
        y = [0.0] * len(x)
        for i, x0 in enumerate(x):
            out = (b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0
            y[i] = out
            x2, x1, y2, y1 = x1, x0, y1, out
        return y

    def scale_rms(self, x, target_db):
        rms = math.sqrt(sum(v * v for v in x) / len(x))
        g = 32767 * 10 ** (target_db / 20) / max(rms, 1e-9)
        return [v * g for v in x]

    def speech(self, n, level_db=-24, f0=150):
        """谐波串 + 共振峰 + 音节级幅度调制, 模拟语音。"""
        src = self.noise(n, 1.0)
        carrier = [0.0] * n
        for i in range(n):
            t = i / FS
            v = 0.0
            for h, gh in ((1, 1.0), (2, 0.55), (3, 0.3), (4, 0.18), (6, 0.1)):
                v += gh * math.sin(2 * math.pi * f0 * h * t + 0.3 * h)
            carrier[i] = 0.7 * v + 0.15 * src[i]
        y = self.peak_eq(carrier, 800, 7, 12)
        y = self.peak_eq(y, 1200, 8, 8)
        out = [0.0] * n
        for i, v in enumerate(y):
            env = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(2 * math.pi * 3.2 * i / FS)) ** 0.8
            out[i] = v * env
        return self.scale_rms(out, level_db)

    def whisper(self, n, level_db=-48):
        """摩擦噪声经强共振峰塑形, 模拟低语(电平低于 -45 固定门限)。"""
        src = self.noise(n, 1.0)
        y = self.peak_eq(src, 720, 16, 40)
        y = self.peak_eq(y, 1700, 18, 36)
        y = self.peak_eq(y, 2600, 20, 28)
        out = [0.0] * n
        for i, v in enumerate(y):
            env = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(2 * math.pi * 5.5 * i / FS))
            out[i] = v * env
        return self.scale_rms(out, level_db)

    def ac(self, n, level_db=-38):
        """空调: 轻度低通宽带稳态噪声, 电平高于 -45 固定门限。"""
        y = self.lp(self.noise(n, 1.0), 0.25)
        # 极低频风扇嗡声
        for i in range(n):
            y[i] += 0.12 * math.sin(2 * math.pi * 120 * i / FS)
        return self.scale_rms(y, level_db)

    def quiet(self, n, level_db=-55):
        return self.scale_rms(self.noise(n, 1.0), level_db)

    def clicks(self, n, density=9, level_db=-34, decay=22.0):
        """稀疏衰变脉冲, 模拟键盘敲击; density=每秒击键数。"""
        y = [0.0] * n
        dur = n / FS
        k = 0
        while True:
            t = self.rng.uniform(0.04, dur - 0.05)
            s = int(t * FS)
            for j in range(220):
                if s + j >= n:
                    break
                y[s + j] += 9000 * math.exp(-j / decay) * (self.rng.random() * 2 - 1)
            k += 1
            if k >= int(dur * density):
                break
        return self.scale_rms(y, level_db)

    def breath(self, n, level_db=-50):
        """气口: 短段柔和宽带气流。"""
        y = self.lp(self.noise(n, 1.0), 0.4)
        env = [math.sin(math.pi * i / max(1, n)) ** 0.7 for i in range(n)]
        return self.scale_rms([a * b for a, b in zip(y, env)], level_db)


def mix(*layers):
    n = max(len(l) for l in layers)
    out = [0.0] * n
    for l in layers:
        for i, v in enumerate(l):
            out[i] += v
    return out


def write_wav(path, samples):
    pcm = [int(max(-32767, min(32767, v))) for v in samples]
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(struct.pack("<%dh" % len(pcm), *pcm))


def build(sy, timeline):
    """timeline: [(start,end,kind, builder_fn_name or ('mix',[...]))]"""
    total = int(timeline[-1][1] * FS)
    track = [0.0] * total
    labels = []
    for start, end, kind, fn in timeline:
        n = int((end - start) * FS)
        seg = fn(n)
        s = int(start * FS)
        for i, v in enumerate(seg):
            track[s + i] += v
        labels.append({"start": round(start, 3), "end": round(end, 3), "kind": kind})
    return track, labels


# ---------------------------------------------------------------- 场景一: 28s 综合
def main_scene(sy):
    tl = [
        (0.0, 1.0, "quiet", sy.quiet),
        (1.0, 4.0, "speech", sy.speech),
        (4.0, 4.44, "ac", sy.ac),                     # 长停顿(空调底噪) -> drop
        (4.44, 4.5, "breath", sy.breath),             # 60ms 气口(<=breath_ms) -> 保留并合并
        (4.5, 7.5, "speech", sy.speech),
        (7.5, 10.5, "ac", sy.ac),                     # 空调段(固定门限会误留)
        (10.5, 13.0, "speech", sy.speech),
        (13.0, 15.5, "whisper", sy.whisper),          # 低语(固定门限会误删)
        (15.5, 17.0, "ac", sy.ac),
        (17.0, 20.0, "speech", sy.speech),
        (20.0, 21.5, "keyboard", sy.clicks),          # 孤立敲击段
        (21.5, 24.5, "speech", sy.speech),
        (24.5, 26.0, "quiet", sy.quiet),
        (26.0, 28.0, "ac", sy.ac),
    ]
    track, labels = build(sy, tl)
    human = [
        {"start": 1.0, "end": 4.0},      # 第一句(其后 440ms 空调停顿整段剪除)
        {"start": 4.5, "end": 7.5},      # 第二句, 句首 60ms 气口保留(保护带覆盖)
        {"start": 10.5, "end": 15.5},    # 语音+低语
        {"start": 17.0, "end": 20.0},
        {"start": 21.5, "end": 24.5},
    ]
    return track, labels, human


# ---------------------------------------------------------------- 场景二: 12s 注入专项
def inject_scene(sy):
    tl = [
        (0.0, 3.0, "ac", sy.ac),                       # 纯空调底噪 -> drop
        (3.0, 4.0, "keyboard", sy.clicks),             # 1s 稀疏敲击 -> drop(瞬态)
        (4.0, 6.5, "whisper", sy.whisper),             # 低语 -> keep
        (6.5, 6.94, "quiet", sy.quiet),                # 440ms 安静 -> drop
        (6.94, 7.0, "breath", sy.breath),              # 60ms 气口 -> 保留并合并
        (7.0, 8.2, "speech", sy.speech),               # 语音 -> keep
        (8.2, 8.26, "breath", sy.breath),              # 60ms 句中气口 -> 合并保留
        (8.26, 9.5, "speech", sy.speech),
        (9.5, 12.0, "ac", sy.ac),                      # 空调 -> drop
    ]
    track, labels = build(sy, tl)
    human = [
        {"start": 4.0, "end": 6.5},
        {"start": 6.94, "end": 9.5},
    ]
    return track, labels, human


def main():
    sy = Synth(42)
    expected = {}
    for name, builder in (("main", main_scene), ("inject", inject_scene)):
        track, labels, human = builder(sy)
        wav_path = os.path.join(DATA, name + ".wav")
        write_wav(wav_path, track)
        req = {
            "pcm": name + ".wav",
            "fs": FS,
            "features": {"level_db": -45, "flat": 0.6, "min_ms": 200},
            "human": human,
            "render": name + "_render.wav",
        }
        with open(os.path.join(DATA, name + ".req.json"), "w") as f:
            json.dump(req, f, ensure_ascii=False, indent=2)
        expected[name] = {"segments": labels, "human": human}
        print("wrote %s  %.2fs  segments=%d" % (wav_path, len(track) / FS, len(labels)))
    with open(os.path.join(DATA, "expected.json"), "w") as f:
        json.dump(expected, f, ensure_ascii=False, indent=2)
    print("wrote expected.json")


if __name__ == "__main__":
    main()
