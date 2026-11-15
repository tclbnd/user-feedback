"""验收测试(无第三方依赖): python3 test_compressor.py"""

import json
import math
import os
import random
import subprocess
import sys
import tempfile

from comp import LookaheadCompressor

FS = 48000
HERE = os.path.dirname(os.path.abspath(__file__))


def db(v):
    return 20.0 * math.log10(max(abs(v), 1e-12))


def burst_signal(n=14400, seed=1):
    """安静正弦 + 两个大声突发 + 微小噪声."""
    rng = random.Random(seed)
    x = [0.05 * math.sin(2 * math.pi * 440 * i / FS) for i in range(n)]
    for i in range(2000, 3000):
        x[i] += 0.9 * math.sin(2 * math.pi * 1000 * i / FS)
    for i in range(6000, 6600):
        x[i] += 0.8 * math.sin(2 * math.pi * 700 * i / FS)
    for i in range(n):
        x[i] += 0.0005 * (rng.random() - 0.5)
    return x


def make(**kw):
    p = dict(thresh_db=-20.0, ratio=4.0, knee_db=6.0, attack_ms=2.0,
             release_ms=80.0, lookahead_ms=5.0)
    p.update(kw)
    return LookaheadCompressor(FS, **p)


# 验收 1a: 阶跃过冲 <= 0.5 dB
def test_step_overshoot():
    comp = make(ratio=8.0, attack_ms=2.0, release_ms=120.0, lookahead_ms=20.0)
    n_pre = 4800
    x = [0.0] * n_pre + [1.0] * 9600
    y, gain, stats = comp.process(x, block=256)
    la = comp.lookahead
    steady = sum(abs(v) for v in y[-2000:]) / 2000.0
    peak = max(abs(v) for v in y[n_pre + la:])
    overshoot_db = db(peak) - db(steady)
    assert overshoot_db <= 0.5, "step overshoot %.3f dB" % overshoot_db
    assert stats["triggers"] == 1, stats
    print("  step overshoot = %.4f dB" % overshoot_db)


# 验收 1b: 脉冲过冲 <= 0.5 dB(相对稳态目标增益)
def test_impulse_overshoot():
    comp = make(ratio=8.0, attack_ms=2.0, release_ms=120.0, lookahead_ms=20.0)
    x = [0.0] * 9600
    x[4800] = 1.0
    y, gain, stats = comp.process(x, block=256)
    target_db = -20.0 + 20.0 / 8.0  # 0 dBFS 输入的稳态目标电平 -17.5 dB
    peak_db = db(max(abs(v) for v in y))
    assert abs(peak_db - target_db) <= 0.5, "impulse peak %.3f dB" % peak_db
    print("  impulse peak = %.4f dB (target %.2f dB)" % (peak_db, target_db))


# 验收 1c: 包络与参考(单块处理)逐样本误差 <= 0.1 dB
def test_envelope_vs_reference():
    x = burst_signal()
    ref = make()
    _, ref_g, _ = ref.process(x, block=len(x))  # 参考: 单块
    for bs in (64, 256, 1024):
        comp = make()
        _, g, _ = comp.process(x, block=bs)
        err = max(abs(db(a) - db(b)) for a, b in zip(g, ref_g))
        assert err <= 0.1, "block=%d envelope err %.4f dB" % (bs, err)
    print("  envelope vs reference: max err = 0.0 dB for 64/256/1024")


# 验收 1d: 拐点区无斜率突变(静态曲线 C1 + 动态包络平滑)
def test_knee_no_slope_kink():
    comp = make(knee_db=8.0)
    n = 60001
    step = 60.0 / (n - 1)
    g = [comp.static_gain_db(-60.0 + i * step) for i in range(n)]
    d2 = max(abs(g[i + 1] - 2 * g[i] + g[i - 1]) for i in range(1, n - 1))
    # 硬拐点会产生 ~(1-1/R)*step = 5.6e-4 的二阶差分跳变
    assert d2 < 1e-4, "static knee kink d2=%.2e" % d2
    # 动态: 缓慢扫幅穿过拐点区, 增益包络(dB)二阶差分有界
    comp2 = make(knee_db=8.0, lookahead_ms=5.0)
    x = [0.02 + 0.3 * i / 9600.0 for i in range(9600)]  # 直流缓升穿过阈值
    _, gain, _ = comp2.process(x, block=256)
    gd = [db(v) for v in gain]
    d2d = max(abs(gd[i + 1] - 2 * gd[i] + gd[i - 1]) for i in range(1, len(gd) - 1))
    assert d2d < 1e-3, "dynamic knee kink d2=%.2e" % d2d
    print("  knee slope: static d2=%.2e, dynamic d2=%.2e (no kink)" % (d2, d2d))


# 验收 1e: 增益变化率受限
def test_gain_rate_limited():
    comp = make(ratio=8.0, attack_ms=0.1, release_ms=0.1, lookahead_ms=0.0,
                max_rate_db_per_ms=30.0)
    x = [0.0] * 100 + [1.0] * 2000 + [0.0] * 2000
    _, gain, _ = comp.process(x, block=64)
    gd = [db(v) for v in gain]
    step = max(abs(gd[i + 1] - gd[i]) for i in range(len(gd) - 1))
    assert step <= comp.max_rate + 1e-9, "rate %.4f dB/sample" % step
    print("  max gain rate = %.4f <= %.4f dB/sample" % (step, comp.max_rate))


# 验收 2a: 64/256/1024 块大小增益摘要逐样本一致
def test_block_size_invariant():
    x = burst_signal()
    ref = make()
    ref_y, ref_g, ref_s = ref.process(x, block=len(x))
    for bs in (64, 256, 1024):
        comp = make()
        y, g, s = comp.process(x, block=bs)
        assert g == ref_g, "gain differs at block=%d" % bs
        assert y == ref_y, "out differs at block=%d" % bs
        assert s == ref_s, "stats differ at block=%d" % bs
    print("  block 64/256/1024: gain/out/stats bit-identical")


# 验收 2b: 延迟补偿后主信号相位零偏移
def test_zero_phase_after_latency_compensation():
    x = burst_signal()
    # 无压缩场景: 主路径 = 纯整数采样延迟
    comp = make(thresh_db=10.0, lookahead_ms=5.0)
    y, gain, _ = comp.process(x, block=256)
    la = comp.lookahead
    assert la > 0
    assert all(v == 0.0 for v in y[:la])
    assert y[la:] == x[:-la], "main path is not a pure delay"
    # 有压缩场景: 补偿后逐样本等于 输入*增益(无滤波、零相位)
    comp2 = make(lookahead_ms=5.0)
    y2, g2, _ = comp2.process(x, block=256)
    err = max(abs(y2[la + i] - x[i] * g2[la + i])
              for i in range(len(x) - la))
    assert err < 1e-12, "phase error %.2e" % err
    print("  latency-compensated main path: exact, zero phase shift")


# 触发统计
def test_trigger_stats():
    rng = random.Random(7)
    n = 20000
    x = [0.05 * math.sin(2 * math.pi * 440 * i / FS) for i in range(n)]
    for i in range(2000, 3000):
        x[i] += 0.9 * math.sin(2 * math.pi * 1000 * i / FS)
    for i in range(12000, 12600):
        x[i] += 0.8 * math.sin(2 * math.pi * 700 * i / FS)
    x = [v + 0.0002 * (rng.random() - 0.5) for v in x]
    comp = make(release_ms=30.0)
    _, _, stats = comp.process(x, block=256)
    assert stats["triggers"] == 2, stats
    assert stats["samples_in_compression"] > 0
    assert stats["total_samples"] == n
    assert stats["max_gain_reduction_db"] > 0.0
    assert 0.0 < stats["mean_gain_reduction_db"] <= stats["max_gain_reduction_db"]
    print("  stats:", stats)


# CLI 契约: 输出格式 + digest 跨块大小一致
def test_cli_contract():
    x = burst_signal()
    req = {"sr": FS, "samples": x, "thresh_db": -20, "ratio": 4,
           "attack_ms": 2, "release_ms": 80, "lookahead_ms": 5,
           "block": 64, "seed": 1}
    tmp = tempfile.mkdtemp()
    digests, gains = [], []
    for bs in (64, 256, 1024):
        req["block"] = bs
        inp = os.path.join(tmp, "in_%d.json" % bs)
        outp = os.path.join(tmp, "out_%d.json" % bs)
        with open(inp, "w") as f:
            json.dump(req, f)
        subprocess.check_call([sys.executable, os.path.join(HERE, "comp.py"),
                               inp, outp])
        res = json.load(open(outp))
        assert set(["out", "gain", "stats", "digest"]) <= set(res), res.keys()
        assert "triggers" in res["stats"]
        digests.append(res["digest"])
        gains.append(res["gain"])
    assert digests[0] == digests[1] == digests[2], "digest depends on block"
    assert gains[0] == gains[1] == gains[2], "gain depends on block"
    print("  CLI: digest/gain identical across block 64/256/1024")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("all %d tests passed" % len(tests))
