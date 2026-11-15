# -*- coding: utf-8 -*-
"""静音输入 => 输出仅含量化噪声, 直接检验 NTF 整形方向与理论曲线。"""
import math
import dither
import dsp_lite as dsp

sr = 96000
N = 1 << 17
q = 1.0 / 32768.0
x = [0.0] * N
coef_sets = {"flat": None,
             "A_2_-1": [2.0, -1.0],
             "B_1.5_-0.5": [1.5, -0.5],
             "C_1.8_-0.9": [1.8, -0.9]}

for name, coef in coef_sets.items():
    r = dither.process({"samples": x, "bits": 16, "mode": "tpdf",
                        "shaping": coef, "seed": 11, "block": None})
    lvl = [c * q for c in r["out"]]
    f, p = dsp.welch_psd(lvl, 16384, "boxcar", sr, 0.5)
    p0_2k = dsp.band_psd_db(f, p, 20, 2000)
    p4_8k = dsp.band_psd_db(f, p, 4000, 8000)
    p20_40k = dsp.band_psd_db(f, p, 20000, 40000)
    total = 10 * math.log10(max(dsp.full_band_power(f, p), 1e-20))
    print("%-12s 0.02-2k=%7.2f  4-8k=%7.2f  20-40k=%7.2f  total=%7.2f dBFS"
          % (name, p0_2k, p4_8k, p20_40k, total))

# 理论 NTF 功率增益 vs 实测(以 flat TPDF 为基准), 取若干频点
print("\nf(Hz)  theory_gain_dB  measured_gain_dB (coef A)")
r_flat = dither.process({"samples": x, "bits": 16, "mode": "tpdf",
                         "shaping": None, "seed": 11, "block": None})
r_a = dither.process({"samples": x, "bits": 16, "mode": "tpdf",
                      "shaping": [2.0, -1.0], "seed": 11, "block": None})
ff, pf = dsp.welch_psd([c * q for c in r_flat["out"]], 16384, "boxcar", sr, 0.5)
fa, pa = dsp.welch_psd([c * q for c in r_a["out"]], 16384, "boxcar", sr, 0.5)
for f0 in [1000.0, 4000.0, 8000.0, 16000.0, 30000.0]:
    k = dsp.nearest_bin(ff, f0)
    w = 2.0 * math.pi * f0 / sr
    g_th = abs(1.0 - 2.0 * complex(math.cos(-w), math.sin(-w))
               + complex(math.cos(-2 * w), math.sin(-2 * w))) ** 2
    g_meas = pa[k] / max(pf[k], 1e-30)
    print("%6.0f  %8.2f  %8.2f"
          % (f0, 10 * math.log10(g_th), 10 * math.log10(g_meas)))

# 理论带内平均增益: 数值积分 |NTF|^2 在 4-8 kHz 的均值
def ntf2(a1, a2, w):
    z = complex(math.cos(-w), math.sin(-w))
    return abs(1.0 - a1 * z - a2 * z * z) ** 2

print("\ntheoretical band-average NTF gain (4-8 kHz):")
for name, coef in coef_sets.items():
    if coef is None:
        print("%-12s 0.00 dB (reference)" % name)
        continue
    m = 4001
    acc = 0.0
    for i in range(m):
        f = 4000.0 + 4000.0 * i / (m - 1)
        acc += ntf2(coef[0], coef[1], 2.0 * math.pi * f / sr)
    gavg = acc / m
    print("%-12s %.2f dB" % (name, 10.0 * math.log10(gavg)))