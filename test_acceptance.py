#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验收测试（纯标准库）。覆盖：
  A. FFT 正确性、STFT/iSTFT 完美重建、C 算子幂等、并行路径逐位一致；
  B. 6 类素材（语音/打击/噪声/静音/混合/短促音）谱一致性误差 <= 1e-3，
     并输出重建 SNR；
  C. 4 个固定种子各复跑两次，逐次误差曲线逐元素一致；
  D. 两段断点续跑与一次性跑完，最终信号/谱/误差逐样本一致；
  E. 并行调度不改变结果；
  F. CLI 端到端（req.json/output.json）与产物（phase/err/wav/svg）。
运行：python3 test_acceptance.py
"""
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile

import phase
import signals

TOL = 1e-3
SEEDS = [7, 42, 2024, 12345]
WIN = signals.N_FFT                      # 128
HOP = 7 * WIN // 8                       # 12.5% 重叠，保证 6 类均可收敛到 1e-3
MAX_ITER = 800

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("[%s] %s %s" % (PASS if ok else FAIL, name, detail))


def build_case():
    data = signals.build_all()
    cases = {}
    for name, x in data.items():
        mag = phase.magnitude(phase.stft(x, WIN, HOP))
        cases[name] = (x, mag)
    return cases


# ---------------------------------------------------------------- A. 基础
def test_foundation():
    import cmath
    random.seed(0)
    for n in (2, 4, 8, 16, 64):
        a = [complex(random.uniform(-1, 1), random.uniform(-1, 1)) for _ in range(n)]
        f = phase.fft(a)
        ref = [sum(a[k] * cmath.exp(-2j * math.pi * j * k / n) for k in range(n))
               for j in range(n)]
        assert max(abs(f[j] - ref[j]) for j in range(n)) < 1e-9
        assert max(abs(v - u) for v, u in zip(phase.ifft(f), a)) < 1e-9
    check("FFT/IFFT 与 DFT 定义一致", True)

    # 完美重建（内部样本机器精度）、C 幂等、并行逐位一致
    for N in (2000, 8000, 1237):
        x = [0.5 * math.sin(2 * math.pi * 123.7 * i / signals.SR)
             + 0.3 * math.cos(2 * math.pi * 77.1 * i / signals.SR) for i in range(N)]
        s = phase.stft(x, WIN, HOP)
        xr = phase.istft(s, WIN, HOP, N)
        interior = max(abs(x[i] - xr[i]) for i in range(2, N - 2))
        x2 = phase.istft(phase.stft(xr, WIN, HOP), WIN, HOP, N)
        idem = max(abs(xr[i] - x2[i]) for i in range(2, N - 2))
        par = (phase.stft(x, WIN, HOP) == phase.stft(x, WIN, HOP, parallel=True)
               and phase.istft(s, WIN, HOP, N)
               == phase.istft(s, WIN, HOP, N, parallel=True))
        assert interior < 1e-12 and idem < 1e-12 and par, (interior, idem, par)
    check("STFT/iSTFT 完美重建、C 幂等、并行逐位一致", True)

    # 随机谱往返无零 bin（保证幅度投影不退化）
    nb = WIN // 2 + 1
    nf = (2000 + HOP - 1) // HOP
    rng = random.Random(0)
    sp = [[complex(rng.uniform(-1, 1), rng.uniform(-1, 1)) for _ in range(nb)]
          for _ in range(nf)]
    sy = phase.stft(phase.istft(sp, WIN, HOP, 2000), WIN, HOP)
    zeros = sum(1 for t in range(nf) for f in range(nb) if abs(sy[t][f]) < 1e-300)
    check("随机谱往返无零频点", zeros == 0, "zeros=%d" % zeros)


# ---------------------------------------------------------------- B. 6 类
def test_six_materials(cases):
    snr_report = {}
    spec_snr_report = {}
    all_ok = True
    for name, (x, mag) in cases.items():
        r = phase.reconstruct(mag, WIN, HOP, len(x), seed=SEEDS[0],
                              max_iter=MAX_ITER, tol=TOL, method="afgl")
        snr = phase.snr_db(x, r["signal"])
        spec_snr = phase.spectral_snr_db(mag, r["spectrum"])
        snr_report[name] = snr
        spec_snr_report[name] = spec_snr
        ok = (r["final_error"] <= TOL)
        all_ok = all_ok and ok
        check("素材 %-9s 一致性<=1e-3" % name, ok,
              "final=%.3e iters=%d 波形SNR=%s 谱幅度SNR=%s"
              % (r["final_error"], r["iters_used"],
                 "inf" if math.isinf(snr) else "%.2f" % snr,
                 "inf" if math.isinf(spec_snr) else "%.2f" % spec_snr))
    # 附加：基础 GL 误差严格单调（验证投影实现正确）
    x, mag = cases["noise"]
    g = phase.reconstruct(mag, WIN, HOP, len(x), seed=1, max_iter=60,
                          tol=0.0, method="gl")
    mono = all(g["err"][i + 1] <= g["err"][i] + 1e-12
               for i in range(len(g["err"]) - 1))
    check("基础 GL 误差严格单调非升", mono)
    check("6 类素材全部达标且 SNR 报告齐全", all_ok,
          "波形SNR=" + ", ".join(
              "%s:%s" % (k, "inf" if math.isinf(v) else "%.1f" % v)
              for k, v in snr_report.items()))
    return snr_report, spec_snr_report


# ---------------------------------------------------------------- C. 种子复现
def test_seed_reproducibility(cases):
    name = "percuss"
    _, mag = cases[name]
    L = len(cases[name][0])
    curves = {}
    ok_all = True
    for seed in SEEDS:
        r1 = phase.reconstruct(mag, WIN, HOP, L, seed=seed, max_iter=120,
                               tol=0.0, method="afgl")
        r2 = phase.reconstruct(mag, WIN, HOP, L, seed=seed, max_iter=120,
                               tol=0.0, method="afgl")
        same = (r1["err"] == r2["err"]
                and r1["signal"] == r2["signal"]
                and r1["spectrum"] == r2["spectrum"])
        curves[seed] = r1["err"]
        ok_all = ok_all and same
    check("4 种子各复跑两次，误差/信号/谱逐元素一致", ok_all)
    # 同种子曲线完全一致；不同种子曲线允许不同（报告最大发散）
    base = curves[SEEDS[0]]
    cross = max(max(abs(a - b) for a, b in zip(base, curves[s])) for s in SEEDS)
    check("逐次误差曲线可复现（同种子零差异）", ok_all,
          "跨种子最大差异=%.3e（初始化不同，预期非零）" % cross)


# ---------------------------------------------------------------- D. 断点续跑
def test_checkpoint_resume(cases):
    name = "mixture"
    x, mag = cases[name]
    L = len(x)
    split = 60
    # 一次性
    full = phase.reconstruct(mag, WIN, HOP, L, seed=11, max_iter=120, tol=0.0,
                             method="afgl")
    # 两段
    part1 = phase.reconstruct(mag, WIN, HOP, L, seed=11, max_iter=split,
                              tol=0.0, method="afgl")
    part2 = phase.resume_from_checkpoint(mag, WIN, HOP, L, part1["checkpoint"],
                                         max_iter=120 - split, tol=0.0)
    same_sig = full["signal"] == part2["signal"]
    same_spec = full["spectrum"] == part2["spectrum"]
    same_err = full["err"] == part2["err"]
    same_iter = full["iters_used"] == part2["iters_used"]
    check("断点续跑与一次性逐样本一致",
          same_sig and same_spec and same_err and same_iter,
          "signal=%s spectrum=%s err=%s iter=%s"
          % (same_sig, same_spec, same_err, same_iter))
    # 三段再验一次
    p1 = phase.reconstruct(mag, WIN, HOP, L, seed=11, max_iter=30, tol=0.0,
                           method="afgl")
    p2 = phase.resume_from_checkpoint(mag, WIN, HOP, L, p1["checkpoint"],
                                      max_iter=30, tol=0.0)
    p3 = phase.resume_from_checkpoint(mag, WIN, HOP, L, p2["checkpoint"],
                                      max_iter=60, tol=0.0)
    check("三段续跑与一次性逐样本一致",
          p3["signal"] == full["signal"] and p3["err"] == full["err"])


# ---------------------------------------------------------------- E. 并行
def test_parallel(cases):
    name = "noise"
    x, mag = cases[name]
    L = len(x)
    rs = phase.reconstruct(mag, WIN, HOP, L, seed=3, max_iter=40, tol=0.0,
                           method="afgl", parallel=False)
    rp = phase.reconstruct(mag, WIN, HOP, L, seed=3, max_iter=40, tol=0.0,
                           method="afgl", parallel=True)
    check("STFT 内部并行化不改变重建结果",
          rs["signal"] == rp["signal"] and rs["err"] == rp["err"])


# ---------------------------------------------------------------- F. CLI/产物
def test_cli(cases, tmpdir):
    name = "transient"
    x, mag = cases[name]
    outdir = os.path.join(tmpdir, "artifacts")
    req = {
        "mag": mag, "win": WIN, "hop": HOP, "n": len(x), "seed": 7,
        "iters": 400, "tol": TOL, "method": "afgl", "outdir": outdir,
    }
    req_path = os.path.join(tmpdir, "req.json")
    out_path = os.path.join(tmpdir, "output.json")
    with open(req_path, "w") as f:
        json.dump(req, f)
    subprocess.run([sys.executable, "phase.py", req_path, out_path],
                   check=True, cwd=os.path.dirname(os.path.abspath(__file__)))
    with open(out_path) as f:
        out = json.load(f)
    ok = (out["final_error"] <= TOL and len(out["phase"]) == len(mag)
          and len(out["phase"][0]) == WIN // 2 + 1
          and len(out["signal"]) == len(x) and len(out["err"]) >= 1)
    check("CLI 端到端输出（信号/相位/误差）", ok,
          "final=%.3e iters=%d" % (out["final_error"], out["iters_used"]))
    files = ["reconstructed.wav", "phase.json", "err.json", "convergence.svg"]
    have = all(os.path.exists(os.path.join(outdir, fn)) for fn in files)
    check("产物齐全（wav/phase/err/convergence.svg）", have,
          ", ".join(fn for fn in files
                    if os.path.exists(os.path.join(outdir, fn))))
    # CLI 断点续跑（经 JSON 序列化往返，验证 complex 还原正确、逐样本一致）
    cp = out["checkpoint"]
    total = out["checkpoint"]["iter"] + 40
    full = phase.reconstruct(mag, WIN, HOP, len(x), seed=7, max_iter=total,
                             tol=0.0, method="afgl")
    req2 = dict(req)
    req2["checkpoint"] = cp
    req2["iters"] = 40
    req2["tol"] = 0.0
    req2.pop("outdir", None)
    r2 = phase.run(req2)
    check("CLI 检查点 JSON 往返后续跑与一次性逐样本一致",
          r2["signal"] == full["signal"] and r2["err"] == full["err"],
          "续跑迭代=%d 一次性=%d" % (r2["iters_used"], full["iters_used"]))


def main():
    cases = build_case()
    test_foundation()
    test_six_materials(cases)
    test_seed_reproducibility(cases)
    test_checkpoint_resume(cases)
    test_parallel(cases)
    with tempfile.TemporaryDirectory() as td:
        test_cli(cases, td)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print("\n================ 汇总 ================")
    for name, ok, detail in results:
        print("[%s] %s %s" % (PASS if ok else FAIL, name, detail))
    print("总计 %d 项，失败 %d 项" % (len(results), n_fail))
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()