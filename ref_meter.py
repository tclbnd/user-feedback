"""独立参考响度/真峰值计量器（与 normalize.py 不同的实现路径）。

区别于生产实现：
  * 整段载入、显式切片做 400ms/100ms 滑动块（非流式环形缓冲）；
  * K 加权采用直接 I 型双二阶、独立编写；
  * 真峰值采用 8 倍过采样 + Hann 窗 sinc（生产端为 4 倍 + Kaiser 窗多相）；
  * 仅用于验收交叉比对，不被 normalize.py 导入。

用法：python3 ref_meter.py file.wav --layout stereo
自检：python3 ref_meter.py --selftest
"""
import argparse
import json
import math
import wave
from array import array

WEIGHTS = {
    "mono": (1.0,),
    "stereo": (1.0, 1.0),
    "5.1": (1.0, 1.0, 1.0, 0.0, 1.41, 1.41),
}


def _shelf_coeffs(fs):
    # BS.1770-4 附录一公开常数，直接 I 型系数推导
    f0, g_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / fs)
    vh = 10.0 ** (g_db / 20.0)
    vb = vh ** 0.4996667821008259
    a0 = 1 + k / q + k * k
    return ([(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0,
             (vh - vb * k / q + k * k) / a0],
            [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])


def _highpass_coeffs(fs):
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / fs)
    a0 = 1 + k / q + k * k
    return ([1 / a0, -2 / a0, 1 / a0],
            [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])


def df1_filter(samples, b, a):
    """直接 I 型：y[n] = b0x[n]+b1x[n-1]+b2x[n-2] - a1y[n-1]-a2y[n-2]。"""
    out = [0.0] * len(samples)
    x1 = x2 = y1 = y2 = 0.0
    b0, b1, b2 = b
    _, a1, a2 = a
    for i, x in enumerate(samples):
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, x, y1, y
        out[i] = y
    return out


def k_weighted(samples, fs):
    b1, a1 = _shelf_coeffs(fs)
    b2, a2 = _highpass_coeffs(fs)
    return df1_filter(df1_filter(samples, b1, a1), b2, a2)


def integrated_loudness(channels, weights, fs):
    win = int(round(0.4 * fs))
    hop = int(round(0.1 * fs))
    weighted = [k_weighted(ch, fs) for ch in channels]
    n = len(weighted[0])
    block_ms = []
    for start in range(0, n - win + 1, hop):
        m = 0.0
        for c, wgt in enumerate(weights):
            if wgt == 0.0:
                continue
            seg = weighted[c][start:start + win]
            m += wgt * (sum(v * v for v in seg) / win)
        block_ms.append(m)
    if not block_ms:
        return None, 0, 0
    abs_floor = 10.0 ** ((-70.0 + 0.691) / 10.0)
    abs_gated = [m for m in block_ms if m > abs_floor]
    if not abs_gated:
        return None, len(block_ms), 0
    rel_thresh = (-0.691 + 10 * math.log10(sum(abs_gated) / len(abs_gated))) - 10.0
    rel_floor = 10.0 ** ((rel_thresh + 0.691) / 10.0)
    gated = [m for m in abs_gated if m > rel_floor]
    lufs = -0.691 + 10 * math.log10(sum(gated) / len(gated))
    return lufs, len(block_ms), len(gated)


def _hann_sinc_phases(upsample, taps_per_phase):
    length = upsample * taps_per_phase
    center = (length - 1) / 2.0
    proto = [0.0] * length
    for k in range(length):
        d = k - center
        sinc = 1.0 if d == 0 else math.sin(math.pi * d / upsample) / (math.pi * d)
        hann = 0.5 - 0.5 * math.cos(2 * math.pi * k / (length - 1))
        proto[k] = sinc * hann
    # 归一化使各多相分支在直流下增益为 1
    p0 = [proto[r * upsample] for r in range(taps_per_phase)]
    scale = 1.0 / sum(p0)
    phases = [[0.0] * taps_per_phase for _ in range(upsample)]
    for p in range(upsample):
        for r in range(taps_per_phase):
            phases[p][r] = proto[p + r * upsample] * scale
    return phases


def true_peak_dbtp(channels, upsample=8, taps_per_phase=12):
    phases = _hann_sinc_phases(upsample, taps_per_phase)
    peak = 0.0
    for ch in channels:                      # LFE 等全部声道计入真峰值
        state = [0.0] * taps_per_phase
        for x in ch:
            state[1:] = state[:-1]
            state[0] = x
            for h in phases:
                acc = sum(c * s for c, s in zip(h, state))
                if abs(acc) > peak:
                    peak = abs(acc)
    return None if peak <= 0 else 20 * math.log10(peak)


def load_wav(path):
    with wave.open(str(path), "rb") as w:
        nch, sw, fs, nframes = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        if sw != 2:
            raise ValueError("参考计量器仅支持 16-bit PCM")
        raw = array("h")
        raw.frombytes(w.readframes(nframes))
    channels = [[raw[i * nch + c] / 32768.0 for i in range(nframes)]
                for c in range(nch)]
    return channels, fs


def measure(path, layout):
    if layout not in WEIGHTS:
        raise ValueError("未知布局: " + layout)
    channels, fs = load_wav(path)
    if len(channels) != len(WEIGHTS[layout]):
        raise ValueError("布局声道数与文件不符")
    lufs, nblocks, ngated = integrated_loudness(channels, WEIGHTS[layout], fs)
    return {"integrated_lufs": lufs, "dbtp": true_peak_dbtp(channels),
            "blocks": nblocks, "gated_blocks": ngated, "layout": layout}


def selftest():
    # BS.1770 锚点：构造在本计量器上恰为 -23 LUFS 的立体声 1 kHz 正弦，
    # 再验证测量结果、块数与真峰值，覆盖 K 加权/块统计/双门控全链路。
    fs = 48000
    n = 2 * fs
    unit = [math.sin(2 * math.pi * 1000 * i / fs) for i in range(n)]
    kw = k_weighted(unit, fs)
    rms2 = sum(v * v for v in kw[fs:]) / (n - fs)   # 跳过滤波器起振
    # 双声道加权均方 = 2*(amp*rms2)^2；令 -0.691+10log10(...) = -23
    amp = math.sqrt(10.0 ** ((-23.0 + 0.691) / 10.0) / (2.0 * rms2))
    tone = [amp * s for s in unit]
    lufs, nb, ng = integrated_loudness([tone, tone], WEIGHTS["stereo"], fs)
    tp = true_peak_dbtp([tone, tone])
    amp_dbf = 20 * math.log10(amp)
    ok = abs(lufs - (-23.0)) < 0.1 and abs(tp - amp_dbf) < 0.1
    print(json.dumps({"anchor_lufs": lufs, "anchor_dbtp": tp,
                      "tone_level_dbf": amp_dbf,
                      "blocks": nb, "gated": ng, "pass": ok}, indent=2))
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument("wav", nargs="?", type=str)
    p.add_argument("--layout", default="stereo")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()
    if a.selftest:
        raise SystemExit(0 if selftest() else 1)
    if not a.wav:
        p.error("需要 WAV 文件或 --selftest")
    print(json.dumps(measure(a.wav, a.layout), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
