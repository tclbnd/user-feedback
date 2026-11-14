#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端演示：对 6 类素材做幅度-only 相位迭代重建，产出全部交付物到 outputs/。
  outputs/<素材>/
      phase.json          相位谱（弧度，帧 x 频点）
      reconstructed.wav   重建音频（16-bit PCM, 8kHz）
      err.json            逐次谱一致性误差
      convergence.svg     收敛曲线（log 纵轴，含 1e-3 参考线）
  outputs/summary.json    6 类素材的最终误差、迭代数、SNR、是否达标
  outputs/convergence_all.svg   6 类素材收敛曲线对比
  outputs/reproducibility.svg   4 个种子的逐次误差曲线
运行：python3 demo.py
"""
import json
import math
import os

import phase
import signals

WIN = signals.N_FFT
HOP = 7 * WIN // 8
TOL = 1e-3
MAX_ITER = 800
SEEDS = [7, 42, 2024, 12345]
OUT = "outputs"


def main():
    os.makedirs(OUT, exist_ok=True)
    data = signals.build_all()
    summary = {}
    all_curves = {}

    for name, x in data.items():
        d = os.path.join(OUT, name)
        os.makedirs(d, exist_ok=True)
        mag = phase.magnitude(phase.stft(x, WIN, HOP))
        result = phase.reconstruct(mag, WIN, HOP, len(x), seed=SEEDS[0],
                                   max_iter=MAX_ITER, tol=TOL, method="afgl")
        snr = phase.snr_db(x, result["signal"])
        spec_snr = phase.spectral_snr_db(mag, result["spectrum"])

        phase.save_phase_json(os.path.join(d, "phase.json"), result["spectrum"])
        phase.save_wav(os.path.join(d, "reconstructed.wav"),
                       result["signal"], sr=signals.SR)
        with open(os.path.join(d, "err.json"), "w") as f:
            json.dump(result["err"], f)
        phase.plot_convergence_svg(os.path.join(d, "convergence.svg"),
                                   {name: result["err"]})

        all_curves[name] = result["err"]
        summary[name] = {
            "frames": len(mag),
            "bins": WIN // 2 + 1,
            "iters_used": result["iters_used"],
            "final_error": result["final_error"],
            "converged": result["converged"],
            "waveform_snr_db": None if math.isinf(snr) else snr,
            "spectral_snr_db": None if math.isinf(spec_snr) else spec_snr,
            "silence": math.isinf(snr),
            "meets_tol_1e-3": result["final_error"] <= TOL,
        }
        print("%-9s iters=%3d final=%.3e waveformSNR=%s spectralSNR=%s"
              % (name, result["iters_used"], result["final_error"],
                 "inf(silence)" if math.isinf(snr) else "%.2fdB" % snr,
                 "inf(silence)" if math.isinf(spec_snr)
                 else "%.2fdB" % spec_snr))

    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump({"win": WIN, "hop": HOP, "overlap": 1 - HOP / WIN,
                   "tol": TOL, "max_iter": MAX_ITER, "method": "afgl",
                   "materials": summary}, f, indent=2)
    phase.plot_convergence_svg(os.path.join(OUT, "convergence_all.svg"),
                               all_curves, title="6 materials convergence")

    # 可复现性：4 个种子的逐次误差曲线（同一素材）
    x = data["percuss"]
    mag = phase.magnitude(phase.stft(x, WIN, HOP))
    seed_curves = {}
    for seed in SEEDS:
        r = phase.reconstruct(mag, WIN, HOP, len(x), seed=seed,
                              max_iter=120, tol=0.0, method="afgl")
        seed_curves["seed%d" % seed] = r["err"]
    phase.plot_convergence_svg(os.path.join(OUT, "reproducibility.svg"),
                               seed_curves, title="4 seeds reproducibility")

    # 断点续跑演示（两段 vs 一次性），写入 JSON 证明逐样本一致
    full = phase.reconstruct(mag, WIN, HOP, len(x), seed=SEEDS[0],
                             max_iter=120, tol=0.0, method="afgl")
    p1 = phase.reconstruct(mag, WIN, HOP, len(x), seed=SEEDS[0],
                           max_iter=60, tol=0.0, method="afgl")
    p2 = phase.resume_from_checkpoint(mag, WIN, HOP, len(x), p1["checkpoint"],
                                     max_iter=60, tol=0.0)
    ckpt_demo = {
        "one_shot_iters": full["iters_used"],
        "two_stage_iters": p2["iters_used"],
        "signal_identical": full["signal"] == p2["signal"],
        "spectrum_identical": full["spectrum"] == p2["spectrum"],
        "err_identical": full["err"] == p2["err"],
    }
    with open(os.path.join(OUT, "checkpoint_demo.json"), "w") as f:
        json.dump(ckpt_demo, f, indent=2)

    n_ok = sum(1 for v in summary.values() if v["meets_tol_1e-3"])
    print("\n达标 %d/6；产物目录：%s" % (n_ok, os.path.abspath(OUT)))
    print("断点续跑逐样本一致：", ckpt_demo["signal_identical"])


if __name__ == "__main__":
    main()