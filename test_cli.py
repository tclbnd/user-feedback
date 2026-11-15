"""CLI 契约与边缘帧规则测试（不依赖第三方库）。"""
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import pitch  # noqa: E402

RATE = 16000


def make_signal():
    n = RATE  # 1 s，前 0.3s 静音，其后 200Hz 稳态
    x = [0.0] * n
    for i in range(int(0.3 * RATE), n):
        x[i] = 0.7 * math.sin(2 * math.pi * 200.0 * i / RATE)
    return x


def main():
    x = make_signal()
    failures = []

    # 1) 只输出完整帧：offset 序列与帧数
    for fs, hop in ((2048, 512), (4096, 1024), (512, 256)):
        res = pitch.analyze(x, RATE, fs, hop)
        expected_offsets = list(range(0, len(x) - fs + 1, hop))
        got_offsets = [f["offset"] for f in res["frames"]]
        if got_offsets != expected_offsets:
            failures.append(f"fs={fs}: 完整帧 offset 序列不符")
        # schema
        for f in res["frames"]:
            assert set(f) == {"offset", "voiced", "hz", "confidence"}, f.keys()
            assert f["offset"] % hop == 0
            assert isinstance(f["voiced"], bool)
            assert 0.0 <= f["confidence"] <= 1.0
            if f["voiced"]:
                assert pitch.F_MIN <= f["hz"] <= pitch.F_MAX
            else:
                assert f["hz"] is None and f["confidence"] == 0.0
        # stats 必备字段
        for key in ("voiced", "unvoiced", "median_hz", "octave_jump_count",
                    "median_confidence", "median_cent_deviation",
                    "adjacent_jump_count", "frame_size", "hop_size"):
            assert key in res["stats"], key
        assert res["stats"]["voiced"] + res["stats"]["unvoiced"] == len(res["frames"])
        assert len(res["digest"]) == 64

    # 2) 短于一帧：返回空 frames，不报错
    short = pitch.analyze(x[:100], RATE, 2048, 512)
    assert short["frames"] == [] and short["stats"]["voiced"] == 0

    # 3) 窗口中心对齐：两种帧长在同一原始时间点的估计应一致
    r1 = pitch.analyze(x, RATE, 2048, 512)
    r2 = pitch.analyze(x, RATE, 4096, 512)
    half1, half2 = (2048 - 1) / 2.0, (4096 - 1) / 2.0
    c1 = {round((f["offset"] + half1) / RATE, 4): f for f in r1["frames"]}
    c2 = {round((f["offset"] + half2) / RATE, 4): f for f in r2["frames"]}
    common = sorted(set(c1) & set(c2))
    steady = [t for t in common if t > 0.55]
    devs = [abs(c1[t]["hz"] - c2[t]["hz"]) / c1[t]["hz"]
            for t in steady if c1[t]["voiced"] and c2[t]["voiced"]]
    if not devs or max(devs) > 0.01:
        failures.append(f"窗口中心对齐偏差过大: {devs[:3]}")

    # 4) 确定性：同输入两次 digest 相同
    assert pitch.analyze(x, RATE, 2048, 512)["digest"] == r1["digest"]

    # 5) 纯静音不应产生有声帧（弱信号虚假基频）
    silence = [0.0] * RATE
    rs = pitch.analyze(silence, RATE, 2048, 512)
    if rs["stats"]["voiced"] != 0:
        failures.append("纯静音出现虚假有声帧")
    # 去相关白噪声：用大质数模数的 LCG 并取高位，避免低位周期形成假基频
    state = 123456789
    rng_noise = []
    for _ in range(RATE):
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        rng_noise.append((state >> 40) / (1 << 23) - 1.0)
    rn = pitch.analyze(rng_noise, RATE, 2048, 512)
    rate_false = rn["stats"]["voiced"] / max(1, len(rn["frames"]))
    print(f"白噪声有声占比 = {rate_false * 100:.2f}% ({rn['stats']['voiced']}/{len(rn['frames'])})")
    if rate_false > 0.05:
        failures.append("白噪声虚假有声帧超 5%")

    # 6) 子进程 CLI：input.json -> output.json
    inp = HERE / "input.json"
    out = HERE / "output.json"
    inp.write_text(json.dumps(dict(sample_rate=RATE, frame_size=2048,
                                   hop_size=512, samples=x)))
    proc = subprocess.run([sys.executable, str(HERE / "pitch.py"),
                           str(inp), str(out)], capture_output=True, text=True)
    if proc.returncode != 0:
        failures.append(f"CLI 失败: {proc.stderr}")
    else:
        disk = json.loads(out.read_text())
        assert disk["digest"] == r1["digest"]
        assert len(disk["frames"]) == len(r1["frames"])
    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)

    if failures:
        print("CLI TEST FAIL:")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print("CLI/边缘帧/确定性/弱信号 测试全部通过；中心对齐最大偏差 %.4f%%"
          % (100 * max(devs)))


if __name__ == "__main__":
    main()
