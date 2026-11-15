"""八度纠偏专项：强二次谐波（倍频误报）与低基频（亚谐波保留）。"""
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pitch  # noqa: E402

RATE = 16000


def tone(f0, harmonics, seconds=1.0):
    n = int(RATE * seconds)
    out = []
    phase = 0.0
    for _ in range(n):
        s = sum(a * math.sin(h * phase + 0.3 * h) for h, a in harmonics)
        out.append(s)
        phase += 2 * math.pi * f0 / RATE
    peak = max(abs(v) for v in out)
    return [0.8 * v / peak for v in out]


def median_hz(res):
    hz = [f["hz"] for f in res["frames"] if f["voiced"]]
    return pitch._median(hz), len(hz), len(res["frames"])


def main():
    failures = []

    # 1) 强二次谐波：H2 比基频强 6dB，朴素 YIN 易报 2*f0
    sig = tone(140.0, [(1, 0.5), (2, 1.0), (3, 0.3)])
    for fs in (2048, 4096):
        med, nv, nt = median_hz(pitch.analyze(sig, RATE, fs, 512))
        err = abs(med - 140.0) / 140.0
        print(f"强H2  fs={fs}: 中位 {med:6.2f} Hz 误差 {err * 100:4.2f}% 有声 {nv}/{nt}")
        if err > 0.01:
            failures.append(f"强H2 fs={fs} 八度误报")

    # 2) 低基频 85 Hz：不能被抬升到 170 Hz
    sig = tone(85.0, [(1, 1.0), (2, 0.35), (3, 0.15)])
    for fs in (2048, 4096):
        med, nv, nt = median_hz(pitch.analyze(sig, RATE, fs, 512))
        err = abs(med - 85.0) / 85.0
        print(f"低基频 fs={fs}: 中位 {med:6.2f} Hz 误差 {err * 100:4.2f}% 有声 {nv}/{nt}")
        if err > 0.01:
            failures.append(f"低基频 fs={fs} 被抬升")

    # 3) 70 Hz 工作范围下界附近
    sig = tone(72.0, [(1, 1.0), (2, 0.4)])
    med, nv, nt = median_hz(pitch.analyze(sig, RATE, 4096, 512))
    err = abs(med - 72.0) / 72.0
    print(f"下界 72Hz: 中位 {med:6.2f} Hz 误差 {err * 100:4.2f}% 有声 {nv}/{nt}")
    if err > 0.01:
        failures.append("下界频率跟踪失败")

    # 4) 亚谐波证据保留：构造在 2τ 处也有深谷的信号（含 1/2 频率成分）
    n = RATE
    sig2 = []
    phase = 0.0
    for _ in range(n):
        # 主周期 100Hz，同时混入 50Hz 能量制造 2τ 谷，但基频仍应判 100Hz
        sig2.append(0.7 * math.sin(phase) + 0.35 * math.sin(phase / 2.0))
        phase += 2 * math.pi * 100.0 / RATE
    frame = sig2[:4096]
    tmax = RATE // 70
    cmnd = pitch.cumulative_mean_normalized(pitch.difference_function(frame, tmax))
    cand = pitch.refine_period(cmnd, max(2, RATE // 600), tmax)
    print(f"亚谐波证据: tau={cand['tau']:.2f} subharmonic={cand['subharmonic']} "
          f"conf={cand['confidence']:.3f} hz={RATE / cand['tau']:.1f}")
    if cand["tau"] is None:
        failures.append("亚谐波周期缺失")
    elif not (RATE / 105 <= cand["tau"] <= RATE / 95):
        failures.append("应优先低周期(100Hz)解释，而非降到 50Hz")
    med, nv, nt = median_hz(pitch.analyze(sig2, RATE, 4096, 512))
    print(f"含亚谐波信号整段中位基频: {med:.2f} Hz（应≈100）")
    if abs(med - 100.0) / 100.0 > 0.02:
        failures.append("含亚谐波信号整段基频被拉低")

    if failures:
        print("OCTAVE TEST FAIL:", failures)
        sys.exit(1)
    print("八度纠偏专项全部通过")


if __name__ == "__main__":
    main()
