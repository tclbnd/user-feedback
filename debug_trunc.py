# -*- coding: utf-8 -*-
"""验证任务前提: 无抖动直接截位随位深/电平产生谐波, TPDF 则把谐波打散成噪声。"""
import math
import dither
import dsp_lite as dsp

fs = 96000
N = 1 << 18

def worst_harm(bits, level_db, mode, shaping=None):
    amp = 10.0 ** (level_db / 20.0)
    x = [amp * math.sin(2.0 * math.pi * 1000.0 * n / fs) for n in range(N)]
    r = dither.process({"samples": x, "bits": bits, "mode": mode,
                        "shaping": shaping, "seed": 99, "block": None})
    q = 1.0 / (1 << (bits - 1))
    y = [c * q for c in r["out"]]
    th = dsp.coherent_thdn(y, fs, 1000.0)
    wh = max(h[2] for h in th["harmonics"][1:])
    return wh, th["thd_plus_n_db"]

print("bits level  mode        worstHarm_dBFS  THD+N_dB")
for bits in (12, 16, 20):
    for lvl in (-60.0, -24.0):
        for mode in ("none", "tpdf"):
            wh, tn = worst_harm(bits, lvl, mode)
            print("%4d %5d  %-10s  %8.1f      %8.1f"
                  % (bits, lvl, mode, wh, tn))
        print()