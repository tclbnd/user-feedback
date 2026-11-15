# -*- coding: utf-8 -*-
import math
import dither
import dsp_lite as dsp

fs = 96000
N = 1 << 20
q = 1.0 / 32768.0
amp = 10.0 ** (-60.0 / 20.0)
tone = [amp * math.sin(2.0 * math.pi * 1000.0 * n / fs) for n in range(N)]
nfft = 1 << 16

# 平坦 TPDF vs 整形 A, 三种 PSD 处理对照
for tag, sh in [("flat", None), ("shaped_A", [2.0, -1.0])]:
    r = dither.process({"samples": tone, "bits": 16, "mode": "tpdf",
                        "shaping": sh, "seed": 20261116, "block": None})
    y = [c * q for c in r["out"]]
    # (1) 原始信号直接 PSD
    f1, p1 = dsp.welch_psd(y, nfft, "boxcar", fs, 0.0)
    # (2) 仅减基波
    res1 = dsp.residual_after_tone(y, fs, 1000.0, harmonics=0)
    f2, p2 = dsp.welch_psd(res1, nfft, "boxcar", fs, 0.0)
    # (3) 减基波+10 谐波
    res10 = dsp.residual_after_tone(y, fs, 1000.0, harmonics=10)
    f3, p3 = dsp.welch_psd(res10, nfft, "boxcar", fs, 0.0)
    # (4) 同 seed 静音基线(真值)
    rs = dither.process({"samples": [0.0] * N, "bits": 16, "mode": "tpdf",
                         "shaping": sh, "seed": 20261116, "block": None})
    ys = [c * q for c in rs["out"]]
    f4, p4 = dsp.welch_psd(ys, nfft, "boxcar", fs, 0.0)
    print("\n== %s ==" % tag)
    for lo, hi in [(1000, 2000), (4000, 8000), (20000, 40000)]:
        print("band %5d-%5d raw %8.2f | subFund %8.2f | sub10 %8.2f | silence %8.2f"
              % (lo, hi,
                 dsp.band_psd_db(f1, p1, lo, hi),
                 dsp.band_psd_db(f2, p2, lo, hi),
                 dsp.band_psd_db(f3, p3, lo, hi),
                 dsp.band_psd_db(f4, p4, lo, hi)))
    # 基波估计精度
    a_est, ph = dsp.estimate_tone(y, fs, 1000.0)
    print("fund amp est=%.8f true=%.8f relerr=%.2e phase=%.4f"
          % (a_est, amp, abs(a_est - amp) / amp, ph))
    # 残差总功率 vs 静音总功率
    pr = sum(v * v for v in res1) / N
    ps = sum(v * v for v in ys) / N
    print("resid1 rms power %.3e  silence rms power %.3e  ratio %.2f dB"
          % (pr, ps, 10 * math.log10(pr / ps)))