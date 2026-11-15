# -*- coding: utf-8 -*-
"""
根因复现: 抖动注入位置决定它是否被噪声传递函数整形。

(A) 普通误差反馈环 u = x + d - H e: 输出恒等式 y = x + d + NTF*e。
    外部抖动 d 走平坦通路 STF=1, 不被整形, 在全频带留下 q^2/6 白噪声地板,
    4-8 kHz 仅下降约 1.7 dB。
(B) 环内抖动二阶 delta-sigma(即 dither.py 生产实现): d 注入量化器输入,
    抖动与量化残差都被 NTF=1-a1 z^-1-a2 z^-2 整形, 4-8 kHz 下降 12-15 dB。

运行: python3 debug_ntf.py
"""
import math
import random
import dsp_lite as dsp

SR = 96000
N = 1 << 17
BITS = 16
Q = 1.0 / (1 << (BITS - 1))
NFFT = 1 << 16
COEF = (2.0, -1.0)


def tpdf(rng):
    return ((rng.random() - 0.5) * Q + (rng.random() - 0.5) * Q)


def error_feedback_external_dither():
    """(A) 抖动在量化器输入端的普通 EF, d 走 STF=1 不整形。"""
    rng = random.Random(11)
    a1, a2 = COEF
    e1 = e2 = 0.0
    out = []
    nmax = 1 << (BITS - 1)
    for _ in range(N):
        d = tpdf(rng)
        u = d - (a1 * e1 + a2 * e2)
        nc = int(math.floor(u / Q + 0.5))
        nc = max(-nmax, min(nmax - 1, nc))
        y = nc * Q
        e = y - u
        e2, e1 = e1, e
        out.append(y)
    return out


def delta_sigma_internal_dither():
    """(B) 环内抖动 DSM, 与 dither.py 生产代码同构。"""
    import dither
    res = dither.process({
        "samples": [0.0] * N, "bits": BITS, "mode": "tpdf",
        "shaping": list(COEF), "seed": 11, "block": None})
    return [c * Q for c in res["out"]]


def flat_tpdf_reference():
    import dither
    res = dither.process({
        "samples": [0.0] * N, "bits": BITS, "mode": "tpdf",
        "shaping": None, "seed": 11, "block": None})
    return [c * Q for c in res["out"]]


def main():
    flat = flat_tpdf_reference()
    ef = error_feedback_external_dither()
    dsm = delta_sigma_internal_dither()
    ff, pf = dsp.welch_psd(flat, NFFT, "boxcar", SR, 0.5)
    _, pe = dsp.welch_psd(ef, NFFT, "boxcar", SR, 0.5)
    _, pd = dsp.welch_psd(dsm, NFFT, "boxcar", SR, 0.5)

    flat_48 = dsp.band_psd_db(ff, pf, 4000, 8000)
    ef_48 = dsp.band_psd_db(ff, pe, 4000, 8000)
    dsm_48 = dsp.band_psd_db(ff, pd, 4000, 8000)
    print("静音输入, 4-8 kHz PSD (dBFS/Hz), 平坦 TPDF 基线 %.2f:" % flat_48)
    print("  (A) EF 外抖动(不整形): %.2f  下降 %.2f dB"
          % (ef_48, flat_48 - ef_48))
    print("  (B) DSM 环内抖动(整形): %.2f  下降 %.2f dB"
          % (dsm_48, flat_48 - dsm_48))
    assert flat_48 - ef_48 < 3.0, "EF 外抖动不应显著整形"
    assert flat_48 - dsm_48 > 12.0, "DSM 环内抖动应深整形"
    print("\n结论: 必须把 TPDF 抖动注入环内, 才能让抖动随 NTF 一起推向高频。")


if __name__ == "__main__":
    main()