#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
6 类确定性测试素材生成（纯标准库）：
  speech    语音：基频 + 谐波 + 缓慢调幅，模拟浊音段（非整频、非平稳）
  percuss   打击乐：指数衰减的脉冲串，瞬态/宽频
  noise     噪声：固定种子的宽频白噪声（带轻微低通）
  silence   静音：全零
  mixture   混合：语音 + 低强度噪声 + 偶发脉冲
  transient 短促音：仅两个极短的包络脉冲（稀疏、短时）
所有素材都混入非整数频率成分以制造 bin 间泄漏，由 STFT 窗自然处理。
"""
import math
import random

SR = 8000
DURATION = 1.0
N = int(SR * DURATION)
N_FFT = 128
HOP = 32


def _ramp(v, n):
    return [v] * n


def gen_speech(n=N, sr=SR, seed=101):
    rng = random.Random(seed)
    f0 = 137.3  # 非整数 bin 基频
    out = [0.0] * n
    # 三段不同基频，模拟语调变化
    bounds = [0, n // 3, 2 * n // 3, n]
    f0s = [f0, 151.7, 128.9]
    for seg in range(3):
        lo, hi = bounds[seg], bounds[seg + 1]
        f = f0s[seg]
        for i in range(lo, hi):
            t = i / sr
            amp_env = 0.5 + 0.5 * math.sin(2 * math.pi * 3.0 * t + seg)
            v = 0.0
            for h in range(1, 7):
                v += (1.0 / h) * math.sin(2 * math.pi * f * h * t
                                          + rng.random() * 0.0)
            out[i] = 0.22 * amp_env * v
    # 轻微时变抖动（确定性）
    for i in range(n):
        out[i] *= 1.0 + 0.05 * math.sin(2 * math.pi * 5.3 * i / sr)
    return out


def gen_percuss(n=N, sr=SR, seed=202):
    rng = random.Random(seed)
    out = [0.0] * n
    # 每 0.125s 一次敲击，指数衰减 + 噪声激励
    period = int(sr * 0.125)
    for start in range(0, n, period):
        decay_len = int(sr * 0.05)
        for k in range(decay_len):
            i = start + k
            if i >= n:
                break
            env = math.exp(-k / (decay_len * 0.25))
            tone = math.sin(2 * math.pi * 213.7 * k / sr)
            nz = rng.uniform(-1.0, 1.0)
            out[i] += 0.6 * env * (0.6 * tone + 0.8 * nz)
    return out


def gen_noise(n=N, sr=SR, seed=303):
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(n)]
    # 一阶低通，仍然宽频
    out = [0.0] * n
    prev = 0.0
    for i in range(n):
        prev = 0.55 * prev + 0.45 * raw[i]
        out[i] = 0.5 * prev
    return out


def gen_silence(n=N, **kw):
    return [0.0] * n


def gen_mixture(n=N, sr=SR, seed=404):
    sp = gen_speech(n, sr, seed=101)
    nz = gen_noise(n, sr, seed=303)
    out = [0.0] * n
    rng = random.Random(seed)
    # 偶发打击脉冲
    for _ in range(6):
        start = rng.randrange(0, n - 200)
        for k in range(120):
            env = math.exp(-k / 30.0)
            out[start + k] += 0.4 * env * rng.uniform(-1, 1)
    for i in range(n):
        out[i] = 0.7 * sp[i] + 0.25 * nz[i] + out[i]
    return out


def gen_transient(n=N, sr=SR, seed=505):
    rng = random.Random(seed)
    out = [0.0] * n
    for frac in (0.2, 0.65):
        start = int(n * frac)
        length = min(40, n - start)  # 5ms 极短脉冲
        if length <= 0:
            continue
        for k in range(length):
            i = start + k
            env = math.exp(-k / 12.0)
            out[i] = 0.8 * env * (0.7 * math.sin(2 * math.pi * 388.3 * k / sr)
                                  + 0.5 * rng.uniform(-1, 1))
    return out


GENERATORS = [
    ("speech", gen_speech),
    ("percuss", gen_percuss),
    ("noise", gen_noise),
    ("silence", gen_silence),
    ("mixture", gen_mixture),
    ("transient", gen_transient),
]


def build_all(n=N, sr=SR):
    return {name: g(n=n, sr=sr) for name, g in GENERATORS}


if __name__ == "__main__":
    data = build_all()
    for name, x in data.items():
        peak = max(abs(v) for v in x)
        rms = math.sqrt(sum(v * v for v in x) / len(x))
        print("%-9s n=%d peak=%.4f rms=%.4f" % (name, len(x), peak, rms))
