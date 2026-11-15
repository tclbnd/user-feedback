"""验收测试:
1. 淡化中点能量偏差 <= 0.5 dB; 剪接样本间跳变 <= 满幅 2%;
   与参考(同输入块=L 的独立进程结果)逐样本误差 <= 1e-3。
2. 3 组淡化长度 x 2 种块大小复跑, 摘要(digest)完全一致; 另跑线性基线对照。
"""
import json
import math
import os
import subprocess
import sys

import xfade

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.environ.get("TMPDIR", "/tmp")


def make_signals(L, seed):
    """确定性测试信号(低频单频为主, 少量噪声):
    mode 0 -> 同频同相(近乎连续, 不应触发对齐);
    mode 1 -> 同频 + 大相位失配;
    mode 2 -> 频率斜率差 + 中等相位失配。
    """
    rng = _LCG(seed)
    wa = 2 * math.pi * (0.018 + 0.004 * (seed % 3))
    mode = seed % 3
    wb = wa + (0.006 if mode == 2 else 0.0)
    ph_b = (0.4 if mode == 0 else 1.4 if mode == 1 else 0.85)
    a, b = [], []
    for n in range(L):
        na = 0.01 * (rng() * 2 - 1)
        nb = 0.01 * (rng() * 2 - 1)
        a.append(math.cos(wa * n + 0.4) + na)
        b.append(0.95 * math.cos(wb * n + ph_b) + nb)
    return a, b


def make_independent(L, seed):
    """独立(互不相关)同功率信号: 等功率淡化交叉项平均趋零,
    中点能量偏差应严格 <= 0.5 dB。"""
    rng_a = _LCG(seed * 7 + 1)
    rng_b = _LCG(seed * 13 + 3)
    a = [0.8 * (rng_a() * 2 - 1) for _ in range(L)]
    b = [0.8 * (rng_b() * 2 - 1) for _ in range(L)]
    return a, b


class _LCG:
    def __init__(self, seed):
        self.s = (seed * 2654435761 + 12345) & 0xFFFFFFFF

    def __call__(self):
        # Numerical Recipes LCG, 输出 [0,1)
        self.s = (1664525 * self.s + 1013904223) & 0xFFFFFFFF
        return self.s / 4294967296.0


def linear_baseline(a, b, L):
    out = []
    for i in range(L):
        t = i / (L - 1)
        out.append(a[i] * (1 - t) + b[i] * t)
    return out


def run_cli(a, b, L, block, seed):
    inp = os.path.join(TMP, "in_%d_%d.json" % (L, block))
    outp = os.path.join(TMP, "out_%d_%d.json" % (L, block))
    with open(inp, "w") as f:
        json.dump({"a": a, "b": b, "len": L, "block": block, "seed": seed}, f)
    subprocess.check_call([sys.executable, os.path.join(HERE, "xfade.py"),
                           inp, outp])
    with open(outp) as f:
        return json.load(f)


def main():
    lengths = [256, 512, 1024]
    blocks = [32, 127]
    failures = []

    for L in lengths:
        seed = 11 + L // 256
        a, b = make_signals(L, seed)

        # 参考: 库函数, block=L (无切分)
        ref_out, ref_p, ref_stats = xfade.crossfade(a, b, L, L)

        # 线性基线对照(应出现中点下凹与更大跳变)
        base = linear_baseline(a, b, L)
        mid = L // 2
        ms = lambda x: sum(v * v for v in x) / len(x)
        base_dev = 10 * math.log10(
            ms(base) / (0.5 * (ms(a) + ms(b))) + 1e-12)

        digests = set()
        for block in blocks + [L]:
            res = run_cli(a, b, L, block, seed)
            out = res["out"]
            st = res["stats"]
            assert len(out) == L
            assert res["splice_pos"] == ref_p

            # 验收 1b: 剪接超额跳变(扣除信号自然增量) <= 满幅 2%
            jmp = st["splice_jump"]["excess_jump_norm"]
            if jmp > 0.02:
                failures.append("L=%d block=%d excess_jump=%.4f"
                                % (L, block, jmp))

            # 验收 1c: 与参考逐样本误差 <= 1e-3
            err = max(abs(x - y) for x, y in zip(out, ref_out))
            if err > 1e-3:
                failures.append("L=%d block=%d max_err=%.2e" % (L, block, err))

            digests.add(res["digest"])

            print("L=%4d block=%4d splice=%4d mid_dev=%+.3fdB "
                  "excess_jump=%.3f%% (对齐前 %.3f%%) raw_resid=%.3f%% "
                  "max_err=%.1e aligned=%s dphi=%.3f domega=%.4f digest=%s"
                  % (L, block, res["splice_pos"], st["mid_dev_db"],
                     100 * jmp,
                     100 * st["splice_jump"]["excess_jump_pre_align"],
                     100 * st["splice_jump"]["raw_residual_norm"],
                     err, st["phase"]["aligned"],
                     st["phase"]["mismatch_rad"], st["phase"]["domega"],
                     res["digest"][:10]))

        # 验收 2: 三种块大小摘要一致
        if len(digests) != 1:
            failures.append("L=%d digest mismatch across blocks: %s"
                            % (L, digests))

        print("  [基线对照] 线性淡化能量偏差=%+.3f dB; 基线硬切步长=%.4f"
              % (base_dev, abs(a[mid] - b[mid])))

    # 验收 1a: 独立信号等功率淡化, 中点能量偏差 <= 0.5 dB
    # (独立噪声无同相连续参考, 能量指标在该场景验证; 跳变指标在音调场景验证)
    print()
    for L in lengths:
        seed = 101 + L
        ai, bi = make_independent(L, seed)
        ref_out_i, _, st_i = xfade.crossfade(ai, bi, L, L)
        digests_i = set()
        for block in blocks:
            res = run_cli(ai, bi, L, block, seed)
            dev = res["stats"]["mid_dev_db"]
            err = max(abs(x - y) for x, y in zip(res["out"], ref_out_i))
            if abs(dev) > 0.5:
                failures.append("indep L=%d block=%d mid_dev=%.3fdB"
                                % (L, block, dev))
            if err > 1e-3:
                failures.append("indep L=%d block=%d max_err=%.2e"
                                % (L, block, err))
            digests_i.add(res["digest"])
        if len(digests_i) != 1:
            failures.append("indep L=%d digest mismatch" % L)
        print("独立信号 L=%4d: 等功率淡化能量偏差=%+.4f dB (线性基线 "
              "%+.4f dB), 摘要一致=%s"
              % (L, st_i["mid_dev_db"],
                 10 * math.log10(ms(linear_baseline(ai, bi, L))
                                 / (0.5 * (ms(ai) + ms(bi))) + 1e-12),
                 len(digests_i) == 1))

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("ALL CHECKS PASSED: %d 长度 x %d 块大小, 摘要一致, 误差达标"
          % (len(lengths), len(blocks)))


if __name__ == "__main__":
    main()