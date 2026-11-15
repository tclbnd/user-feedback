# -*- coding: utf-8 -*-
import math
import dither
import dsp_lite as dsp

sr = 96000
N = 16384
q = 1.0 / 32768.0
x = [0.3 * math.sin(2.0 * math.pi * 1000.0 * n / sr) for n in range(N)]

for mode, sh in [("none", None), ("rect", None), ("tpdf", None),
                 ("tpdf", [2.0, -1.0]), ("tpdf", [1.8, -0.9])]:
    r = dither.process({"samples": x, "bits": 16, "mode": mode,
                        "shaping": sh, "seed": 7, "block": None})
    s = r["stats"]
    print(mode, sh,
          "err_var/q^2=%.4f" % (s["err_var"] / q ** 2),
          "clip", s["n_clipped"],
          "arch", s["architecture"])

r1 = dither.process({"samples": x, "bits": 16, "mode": "tpdf",
                     "shaping": [1.8, -0.9], "seed": 7, "block": None})
r2 = dither.process({"samples": x, "bits": 16, "mode": "tpdf",
                     "shaping": [1.8, -0.9], "seed": 7, "block": 1000})
r3 = dither.process({"samples": x, "bits": 16, "mode": "tpdf",
                     "shaping": [1.8, -0.9], "seed": 7, "block": 4096})
print("bit exact block1000:", r1["out"] == r2["out"],
      "block4096:", r1["out"] == r3["out"],
      "digest:", r1["digest"] == r2["digest"],
      "state_match:", r2["stats"]["final_state"]
      == r1["stats"]["final_state"])

# 静音输入: 整形方向(环内抖动应深压低频)
xs = [0.0] * (1 << 16)
for sh in (None, [2.0, -1.0], [1.8, -0.9]):
    r = dither.process({"samples": xs, "bits": 16, "mode": "tpdf",
                        "shaping": sh, "seed": 7, "block": None})
    lvl = [c * q for c in r["out"]]
    f, p = dsp.welch_psd(lvl, 8192, "boxcar", sr, 0.5)
    print("shaping", sh,
          "PSD 0.02-2k %.2f 4-8k %.2f 20-40k %.2f dBFS/Hz"
          % (dsp.band_psd_db(f, p, 20, 2000),
             dsp.band_psd_db(f, p, 4000, 8000),
             dsp.band_psd_db(f, p, 20000, 40000)))

# FFT 自检: 与直接 DFT 定义比对一个小序列
z = [1.0, 2.0, -1.0, 0.5]
ff = dsp.fft(z)
ref = [sum(z[n] * complex(math.cos(-2 * math.pi * k * n / 4),
                          math.sin(-2 * math.pi * k * n / 4))
         for n in range(4)) for k in range(4)]
err = max(abs(ff[k] - ref[k]) for k in range(4))
print("FFT max abs error vs naive DFT:", err)