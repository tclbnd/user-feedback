import argparse
import math
import struct
import wave

FS = 48000


def _sample(case, t, ch, channels):
    if case == "silence":
        return 0.0
    if case == "limited":
        v = 0.015 * math.sin(2 * math.pi * 430 * t)
        if 1.21 < t < 1.21016:
            v = 0.95 * math.sin(2 * math.pi * 12000 * t + math.pi / 4)
        return v
    if case == "short":
        return 0.2 * math.sin(2 * math.pi * (420 + 17 * ch) * t)
    if case == "intersample":
        # 16 kHz 正弦：采样点峰值约 -6 dBFS，但重建后的采样间峰值接近 0 dBTP
        return 0.5 * math.sin(2 * math.pi * 16000 * t + (ch * math.pi / 3))
    if case == "surround":
        if ch == 3:  # LFE：很响但不参与响度，参与真峰值
            return 0.5 * math.sin(2 * math.pi * 60 * t)
        base = 0.18 * math.sin(2 * math.pi * (300 + 120 * ch) * t)
        # 前段长静音，验证相对门不会被静音段拉低
        return 0.0 if 0.6 < t < 1.9 else base
    # normal（立体声，含 0.72-1.6s 长静音段）
    v = (0.0 if 0.72 < t < 1.6 else 0.16) * math.sin(
        2 * math.pi * (420 + 17 * ch) * t)
    if channels == 6 and ch == 3:
        v = 0.18 * math.sin(2 * math.pi * 60 * t)
    return v


def make(path, seconds=3, channels=2, case="normal"):
    n = round(FS * seconds)
    rows = bytearray()
    for i in range(n):
        t = i / FS
        vals = [round(_sample(case, t, ch, channels) * 32767)
                for ch in range(channels)]
        rows.extend(struct.pack("<" + "h" * channels, *vals))
    with wave.open(str(path), "wb") as f:
        f.setparams((channels, 2, FS, n, "NONE", "not compressed"))
        f.writeframes(rows)


def main():
    p = argparse.ArgumentParser(description="生成响度归一化测试向量")
    p.add_argument("output", nargs="?", default="demo.wav")
    p.add_argument("--case", default="normal",
                   choices=["normal", "silence", "limited", "short",
                            "intersample", "surround"])
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--channels", type=int, default=2)
    a = p.parse_args()
    if a.case == "surround":
        a.channels = 6
    make(a.output, a.seconds, a.channels, a.case)
    print("wrote", a.output, a.case, a.channels, "ch", a.seconds, "s")


if __name__ == "__main__":
    main()

