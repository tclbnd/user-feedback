# -*- coding: utf-8 -*-
"""
TPDF 抖动 + 二阶噪声整形量化器（纯标准库实现）。

用法:
    python3 dither.py input.json output.json

输入 JSON:
  sr:      采样率 Hz，仅记录不参与逐样本运算
  samples: 输入浮点样本, 约定满幅正弦峰值 1.0 即 0 dBFS
  bits:    量化位宽
  mode:    none/rect/tpdf；none=直接截位缺陷基线, rect=矩形抖动, tpdf=TPDF
  shaping: 二阶噪声整形反馈系数 a1 a2，或 null
  block:   分块长度样本，null 或 >=N 表示单块
  seed:    随机源种子, 保证可复现

输出 JSON:
  out: 整数码序列; digest: 全量输出 sha1; stats: 统计信息

== 关键设计: 为什么是“环内抖动”而不是“量化器输入端加抖动”的误差反馈 ==
误差反馈(EF)结构 u = x + d - H e 中, 输出恒等式为 y = x + d + NTF*e,
外部抖动 d 走的是平坦通路(STF=1), 不会被整形, 会在全频带留下 q^2/6 的
白噪声地板, 使 4-8 kHz 降噪量仅约 1.7 dB(本仓库 debug_ntf.py 已实测)。
本实现采用二阶 delta-sigma 噪声整形, 把 TPDF 抖动注入环内量化器输入:
    s1[n] = s1[n-1] + x[n] - y[n-1]
    s2[n] = s2[n-1] + s1[n] - y[n-1] + g1 e[n-1] + g2 e[n-2]
    u     = s2[n] + d[n]                       d: TPDF, 方差 q^2/6
    y[n]  = q * round(u/q), 饱和限幅;  e[n] = y[n] - u[n]
其中 g1 = 2-a1, g2 = 1+a2。z 域线性化可证:
    STF(z) = z^-2
    NTF(z) = 1 - a1 z^-1 - a2 z^-2
即抖动 d 与量化残差 e 都被同一个 NTF 整形, DC 处 NTF(1)=1-a1-a2 接近 0,
量化噪声能量被推向高频。无整形(shaping=null)时退化为普通加抖动量化。

初始条件: s1=s2=yp=e1=e2=0。分块处理时全部状态与 RNG 跨块延续,
随机源是单条连续序列, 因此任意块大小的输出与整段处理逐比特一致。
"""
import json
import sys
import math
import hashlib
import random


class _ShaperCore:
    """有状态的量化核心, 整段与分块两条路径共用, 保证数值完全一致。"""

    def __init__(self, bits, mode, shaping, seed):
        self.bits = bits
        self.mode = mode
        self.q = 1.0 / (1 << (bits - 1))
        self.n_min = -(1 << (bits - 1))
        self.n_max = (1 << (bits - 1)) - 1
        self.rng = random.Random(seed)
        self.shaped = shaping is not None
        if self.shaped:
            a1, a2 = float(shaping[0]), float(shaping[1])
            self.g1 = 2.0 - a1
            self.g2 = 1.0 + a2
            self.a1, self.a2 = a1, a2
        # 环状态(初始条件全部置零)
        self.s1 = 0.0
        self.s2 = 0.0
        self.yp = 0.0      # y[n-1]
        self.e1 = 0.0      # e[n-1]
        self.e2 = 0.0      # e[n-2]
        # 统计量
        self.nclip = 0
        self.sumsq = 0.0
        self.sum_e = 0.0
        self.sum_e2 = 0.0
        self.cnt = 0
        self.out = []

    def _dither(self):
        q = self.q
        if self.mode == "tpdf":
            # 两个独立 U(-q/2,q/2) 之和 => TPDF, 方差 q^2/6
            return ((self.rng.random() - 0.5) * q
                    + (self.rng.random() - 0.5) * q)
        if self.mode == "rect":
            return (self.rng.random() - 0.5) * q
        return 0.0

    def step(self, x):
        q = self.q
        d = self._dither()
        if self.shaped:
            # 二阶 delta-sigma, 环内抖动
            self.s1 = self.s1 + x - self.yp
            self.s2 = (self.s2 + self.s1 - self.yp
                       + self.g1 * self.e1 + self.g2 * self.e2)
            u = self.s2 + d
        else:
            # 普通加抖动量化(无误差反馈)
            u = x + d
        ncode = int(math.floor(u / q + 0.5))     # 对称四舍五入
        if ncode < self.n_min:
            ncode = self.n_min
            self.nclip += 1
        elif ncode > self.n_max:
            ncode = self.n_max
            self.nclip += 1
        y = ncode * q
        e = y - u                                  # 量化器实际误差(含饱和)
        if self.shaped:
            self.yp = y
        # 两种架构都滚动误差历史; 无整形时不参与反馈, 仅保持状态完整
        self.e2, self.e1 = self.e1, e
        self.out.append(ncode)
        self.sumsq += y * y
        self.sum_e += e
        self.sum_e2 += e * e
        self.cnt += 1
        return ncode

    def state(self):
        return {"s1": self.s1, "s2": self.s2, "yp": self.yp,
                "e1": self.e1, "e2": self.e2}

    def stats(self, block_mode):
        n = max(1, self.cnt)
        mean_e = self.sum_e / n
        return {
            "n": self.cnt,
            "q": self.q,
            "rms_code": math.sqrt(
                sum(v * v for v in self.out) / n),
            "rms_level": math.sqrt(self.sumsq / n),
            "err_mean": mean_e,
            "err_rms": math.sqrt(self.sum_e2 / n),
            "err_var": self.sum_e2 / n - mean_e * mean_e,
            "n_clipped": self.nclip,
            "final_state": self.state(),
            "block_mode": block_mode,
            "architecture": ("delta_sigma_internal_dither"
                             if self.shaped else "flat_quantizer"),
        }


def _run(samples, bits, mode, shaping, seed, block):
    if block is None or block >= len(samples):
        core = _ShaperCore(bits, mode, shaping, seed)
        for x in samples:
            core.step(x)
        return core.out, core.stats("single")
    core = _ShaperCore(bits, mode, shaping, seed)
    for start in range(0, len(samples), block):
        end = min(start + block, len(samples))
        for i in range(start, end):
            core.step(samples[i])
    return core.out, core.stats("blocked:" + str(block))


def process(req):
    samples = req["samples"]
    bits = int(req.get("bits", 16))
    mode = req.get("mode", "tpdf")
    shaping = req.get("shaping")
    seed = int(req.get("seed", 1))
    block = req.get("block")
    if block is not None:
        block = int(block)

    out, stats = _run(samples, bits, mode, shaping, seed, block)
    digest = hashlib.sha1(",".join(map(str, out)).encode()).hexdigest()
    return {"out": out, "digest": digest, "stats": stats}


def main():
    with open(sys.argv[1], "r") as f:
        req = json.load(f)
    res = process(req)
    with open(sys.argv[2], "w") as f:
        json.dump(res, f)


if __name__ == "__main__":
    main()
