"""验收复跑：2 组帧长 × 4 组信噪比，带标注的合成信号。

覆盖：
  A 稳定音（110 Hz）
  B 滑音 110 -> 240 -> 130 Hz（检查倍频程跳变）
  C 强二次谐波（基频 140 Hz，H2 比基频强 6 dB，基线必报 280 Hz）
  D 亚谐波/低基频 85 Hz（检查被误抬升一个八度）
  E 中段纯噪声无声段（检查弱信号虚假基频）
"""

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pitch  # noqa: E402

RATE = 16000
SNRS = (40, 20, 10, 3)          # dB，4 组信噪比
FRAME_SIZES = (2048, 4096)      # 2 组帧长
HOP = 512
DURATION = 4.0
N = int(RATE * DURATION)

# (起始秒, 结束秒, f0 函数 或 None 表示无声, 谐波描述)
def f_gliss(t):
    if t < 1.62:
        return 110.0 + (240.0 - 110.0) * (t - 1.15) / (1.62 - 1.15)
    return 240.0 - (240.0 - 130.0) * (t - 1.62) / (2.15 - 1.62)


SEGMENTS = [
    (0.00, 0.50, None, None),                 # 开头长静音（弱信号虚假基频检验）
    (0.50, 1.15, lambda t: 110.0, [(1, 1.0), (2, 0.5), (3, 0.25)]),
    (1.15, 2.15, f_gliss, [(1, 1.0), (2, 0.45), (3, 0.2)]),
    (2.15, 2.60, None, None),                 # 中段无声（噪声）
    (2.60, 3.40, lambda t: 140.0, [(1, 0.5), (2, 1.0), (3, 0.3)]),
    (3.40, 4.00, lambda t: 85.0, [(1, 1.0), (2, 0.35), (3, 0.15)]),
]


def ground_truth(t):
    for a, b, f, _ in SEGMENTS:
        if a <= t < b:
            return None if f is None else f(t)
    return None


def make_clean():
    x = [0.0] * N
    for a, b, f, harmonics in SEGMENTS:
        if f is None:
            continue
        i0, i1 = int(a * RATE), int(b * RATE)
        # 相位累积，保证滑音连续
        phase = 0.0
        prev_f = f(i0 / RATE)
        for i in range(i0, i1):
            t = i / RATE
            cur = f(t)
            phase += 2.0 * math.pi * cur / RATE
            s = 0.0
            for h, amp in harmonics:
                s += amp * math.sin(h * phase + 0.3 * h)
            x[i] = s
            prev_f = cur
    peak = max(abs(v) for v in x) or 1.0
    return [v / peak * 0.8 for v in x]


def add_noise(clean, snr_db, seed=7):
    rng = random.Random(seed)
    noise = [rng.gauss(0.0, 1.0) for _ in range(N)]
    sp = sum(v * v for v in clean) / N
    np_ = sum(v * v for v in noise) / N
    k = math.sqrt(sp / (np_ * 10.0 ** (snr_db / 10.0)))
    return [c + k * z for c, z in zip(clean, noise)]


def frame_label(t_center, frame_size):
    """按窗口覆盖范围给标注：跨段（含越过静音边界）的帧标记为 boundary 不评分。

    边缘帧规则与 pitch.py 一致：只统计完整帧；窗口中心用于时间对齐，
    但有声/无声真值看整窗是否落在同一标注段内。
    """
    half = (frame_size - 1) / 2.0
    t0, t1 = t_center - half / RATE, t_center + half / RATE
    inside = []
    for a, b, f, _ in SEGMENTS:
        if t1 <= a or t0 >= b:
            continue
        inside.append((a, b, f))
    if len(inside) != 1:
        return "boundary", None
    f = inside[0][2]
    if f is None:
        return "unvoiced", None
    return "voiced", f(t_center)


def eval_run(result, frame_size, snr):
    frames = result["frames"]
    half = (frame_size - 1) / 2.0
    errs, stat_errs, gliss_errs = [], [], []
    unvoiced_false = voiced_total = unvoiced_total = skipped = 0
    gliss_jumps = 0
    prev_hz = None
    for fr in frames:
        t = (fr["offset"] + half) / RATE
        kind, ref = frame_label(t, frame_size)
        if kind == "boundary":
            skipped += 1
            prev_hz = None
            continue
        if kind == "unvoiced":
            unvoiced_total += 1
            if fr["voiced"]:
                unvoiced_false += 1
            prev_hz = None
            continue

        voiced_total += 1
        in_gliss = 1.15 <= t < 2.15
        if not fr["voiced"]:
            errs.append(None)  # 漏判记 100% 误差
            prev_hz = None
            continue
        rel = abs(fr["hz"] - ref) / ref
        errs.append(rel)
        (gliss_errs if in_gliss else stat_errs).append(rel)
        if in_gliss:
            if prev_hz is not None:
                ratio = fr["hz"] / prev_hz
                if ratio >= 1.9 or ratio <= 1 / 1.9:
                    gliss_jumps += 1
            prev_hz = fr["hz"]
        else:
            prev_hz = None
    valid = [e for e in errs if e is not None]
    median_err = pitch._median(valid) if valid else 1.0
    miss_rate = sum(1 for e in errs if e is None) / max(1, len(errs))
    false_unvoiced = unvoiced_false / max(1, unvoiced_total)
    return {
        "frame_size": frame_size,
        "snr_db": snr,
        "voiced_ref": voiced_total,
        "unvoiced_ref": unvoiced_total,
        "boundary_skipped": skipped,
        "median_rel_err": median_err,
        "stationary_median_err": pitch._median(stat_errs) if stat_errs else 1.0,
        "gliss_median_err": pitch._median(gliss_errs) if gliss_errs else 1.0,
        "voiced_miss_rate": miss_rate,
        "unvoiced_false_rate": false_unvoiced,
        "gliss_octave_jumps": gliss_jumps,
        "stats_voiced": result["stats"]["voiced"],
        "stats_unvoiced": result["stats"]["unvoiced"],
        "median_hz": result["stats"]["median_hz"],
        "octave_jump_count": result["stats"]["octave_jump_count"],
        "median_confidence": result["stats"]["median_confidence"],
        "energy_gate": result["stats"]["energy_gate"],
    }


def main():
    clean = make_clean()
    summary_rows = []
    for fs in FRAME_SIZES:
        for snr in SNRS:
            sig = add_noise(clean, snr)
            payload = dict(sample_rate=RATE, frame_size=fs, hop_size=HOP,
                           samples=sig)
            result = pitch.analyze(payload["samples"], RATE, fs, HOP)
            row = eval_run(result, fs, snr)
            summary_rows.append(row)
            print(json.dumps(row))
    print("\n=== 判定摘要（阈值：中位误差<=0.5%, 无声误判<=5%, 滑音0倍频跳变）===")
    ok = True
    for r in summary_rows:
        pass_err = r["stationary_median_err"] <= 0.005 and r["voiced_miss_rate"] <= 0.05
        pass_uv = r["unvoiced_false_rate"] <= 0.05
        pass_gl = r["gliss_octave_jumps"] == 0
        line_ok = pass_err and pass_uv and pass_gl
        ok = ok and line_ok
        print(f"fs={r['frame_size']:>4} snr={r['snr_db']:>2}dB "
              f"statErr={r['stationary_median_err'] * 100:6.3f}% "
              f"glissErr={r['gliss_median_err'] * 100:6.3f}% "
              f"miss={r['voiced_miss_rate'] * 100:5.2f}% "
              f"falseUV={r['unvoiced_false_rate'] * 100:5.2f}% "
              f"skip={r['boundary_skipped']:2d} "
              f"glissJmp={r['gliss_octave_jumps']} "
              f"conf={r['median_confidence']:.3f} -> {'PASS' if line_ok else 'FAIL'}")

    # 帧长间一致性：同 SNR 下有声占比与中位基频方向应一致
    print("\n=== 帧长一致性 ===")
    for j, snr in enumerate(SNRS):
        a, b = summary_rows[j], summary_rows[j + len(SNRS)]
        vr_a = a["stats_voiced"] / (a["stats_voiced"] + a["stats_unvoiced"])
        vr_b = b["stats_voiced"] / (b["stats_voiced"] + b["stats_unvoiced"])
        hz_dev = abs(a["median_hz"] - b["median_hz"]) / max(a["median_hz"], 1e-9)
        consistent = abs(vr_a - vr_b) <= 0.08 and hz_dev <= 0.02
        ok = ok and consistent
        print(f"snr={snr:>2}dB voicedRatio {vr_a:.3f}/{vr_b:.3f} "
              f"medianHz {a['median_hz']:.1f}/{b['median_hz']:.1f} "
              f"-> {'CONSISTENT' if consistent else 'DIVERGE'}")
    print("\nOVERALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
