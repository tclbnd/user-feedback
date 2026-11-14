#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FFT 正确性自检 + 各类信号特征探针。"""
import math
import random
import cut

random.seed(7)

# 1) FFT 与朴素 DFT 对比
n = 16
re = [random.uniform(-1, 1) for _ in range(n)]
im = [0.0] * n
ref_re, ref_im = [], []
for k in range(n):
    sr = si = 0.0
    for t in range(n):
        ang = -2 * math.pi * k * t / n
        sr += re[t] * math.cos(ang) - 0
        si += re[t] * math.sin(ang)
    ref_re.append(sr)
    ref_im.append(si)
cut.fft(re, im)
err = max(abs(re[k] - ref_re[k]) + abs(im[k] - ref_im[k]) for k in range(n))
print("FFT max err vs naive DFT:", err)
assert err < 1e-9

FS = 16000
FRAME = 320
FFT_N = 512
window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (FRAME - 1)) for i in range(FRAME)]


def white(dur, amp):
    return [float(random.randint(-amp, amp)) for _ in range(int(FS * dur))]


def lp(x, alpha):
    y = [0.0] * len(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        y[i] = acc
    return y


def hp(x, alpha):
    acc = 0.0
    y = [0.0] * len(x)
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        y[i] = v - acc
    return y


def bp_resonator(x, fc, bw):
    """归一化单位峰值带通双二阶: y[n]=(1-r)(x[n]-r2*x[n-2])-a1*y[n-1]-r2*y[n-2]。"""
    r = math.exp(-math.pi * bw / FS)
    r2 = r * r
    a1 = -2 * r * math.cos(2 * math.pi * fc / FS)
    g = 1 - r
    y1 = y2 = x1 = 0.0
    y = [0.0] * len(x)
    for i, x0 in enumerate(x):
        out = g * (x0 - r2 * x1) - a1 * y1 - r2 * y2
        y[i] = out
        x1, y2, y1 = x0, y1, out
    return y


def peak_eq(x, fc, q, gain_db):
    """RBJ peaking EQ 双二阶, 在 fc 处制造共振峰。"""
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


def scale_rms(x, target_db):
    rms = math.sqrt(sum(v * v for v in x) / len(x))
    g = 32767 * 10 ** (target_db / 20) / max(rms, 1e-9)
    out = [int(max(-32767, min(32767, v * g))) for v in x]
    clip = sum(1 for v in out if abs(v) >= 32767)
    if clip:
        print("  [warn] scale_rms clipping %d/%d samples" % (clip, len(out)))
    return out


def tone(dur, freq, amp, mod=0.0):
    out = []
    for i in range(int(FS * dur)):
        env = 1.0
        if mod:
            env = 0.6 + 0.4 * math.sin(2 * math.pi * mod * i / FS) ** 2
        v = amp * env * math.sin(2 * math.pi * freq * i / FS)
        out.append(int(max(-32767, min(32767, v))))
    return out


def probe(name, x, active_only=False):
    fl, zc, cr, rms = [], [], [], []
    for i in range(0, len(x) - FRAME + 1, FRAME):
        f = cut.frame_features(x[i:i + FRAME], FS, FFT_N, window)
        if active_only and f["rms_db"] < -70:
            continue
        fl.append(f["flat"])
        zc.append(f["zcr"])
        cr.append(f["crest"])
        rms.append(f["rms_db"])
    print("%-18s flat=%.3f zcr=%.3f crest=%.2f rms=%6.1fdB n=%d"
          % (name, sum(fl) / len(fl), sum(zc) / len(zc),
             sum(cr) / len(cr), sum(rms) / len(rms), len(fl)))


# 白噪声(高电平稳态)
probe("white -30dB", scale_rms(white(1.0, 1000), -30))
# 空调: 轻度低通粉化白噪, 保持宽带, 电平 -38dB(高于 -45 固定门限)
ac = lp(white(1.0, 10000), 0.25)
probe("AC pink -38dB", scale_rms(ac, -38))
# 语音样: 多谐波音调 + 幅度调制
sp = []
for i in range(FS):
    v = 0
    for h, g in ((1, 1.0), (2, 0.5), (3, 0.25), (5, 0.12)):
        v += g * math.sin(2 * math.pi * 180 * h * i / FS)
    env = 0.5 + 0.5 * math.sin(2 * math.pi * 3.5 * i / FS) ** 2
    sp.append(v * env * 4000)
probe("speech -24dB", scale_rms(sp, -24))
# 低语: 宽带噪声经声道共振峰 EQ 塑形(f1~700 f2~1700 f3~2600) + 慢调制, 电平 -48dB
src = white(1.0, 10000)
wh = peak_eq(src, 720, 16.0, 40)
wh = peak_eq(wh, 1700, 18.0, 36)
wh = peak_eq(wh, 2600, 20.0, 28)
wh_mod = []
for i, v in enumerate(wh):
    env = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(2 * math.pi * 5.5 * i / FS))
    wh_mod.append(v * env)
whisper = scale_rms(wh_mod, -48)
probe("whisper -48dB", whisper)
# 键盘咔哒: 密集衰变脉冲(仅统计含能量帧)
click = [0.0] * FS
rng = random.Random(3)
for base in (0.02, 0.12, 0.22, 0.34, 0.46, 0.58, 0.7, 0.82, 0.92):
    s = int(base * FS)
    for k in range(200):
        click[s + k] = 9000 * math.exp(-k / 22.0) * (rng.random() * 2 - 1)
click = scale_rms(click, -34)
probe("keyboard clicks", click, active_only=True)
# 安静底噪 -55dB
probe("quiet -55dB", scale_rms(white(1.0, 1000), -55))
print("OK")
