"""侧链预读软拐点压缩器.

用法: python3 comp.py input.json output.json

模型约定(见 README.md):
- 侧链预读 lookahead_ms 提前触发; 主信号路径按同样采样数做纯整数延迟,
  延迟补偿后主信号相位零偏移.
- 增益包络 = 峰值保持检测(滑动最大, 窗口=预读长度) -> 起音/释放平滑
  (时间常数按软拐点曲线连续混合) -> 软拐点压缩律; 增益变化率受硬限幅,
  拐点区无斜率突变, 消除过冲/折痕/泵浦锯齿.
- 输出 {"out":[主信号],"gain":[逐样本线性增益],"stats":{...},"digest":"sha1"};
  处理为逐样本递归, block 仅分块参数, 任意块大小结果逐样本一致.
- seed 被接受; 处理本身完全确定, 不依赖随机数.
"""

import json
import sys
import math
import hashlib
from collections import deque

_EPS = 1e-12


def _to_db(v):
    return 20.0 * math.log10(max(abs(v), _EPS))


class LookaheadCompressor:
    """Lookahead soft-knee compressor, block-size-invariant streaming."""

    def __init__(self, sr, thresh_db=-20.0, ratio=4.0, knee_db=6.0,
                 attack_ms=2.0, release_ms=80.0, lookahead_ms=5.0,
                 max_rate_db_per_ms=60.0, engage_db=0.1):
        self.fs = float(sr)
        self.thresh_db = float(thresh_db)
        self.ratio = float(ratio)
        self.knee_db = max(float(knee_db), 1e-6)
        self.tau_att = max(float(attack_ms), 1e-3) / 1000.0
        self.tau_rel = max(float(release_ms), 1e-3) / 1000.0
        # 可配置触发提前量(整数采样)
        self.lookahead = max(0, int(round(float(lookahead_ms) * self.fs / 1000.0)))
        # 增益变化率硬限制(dB/样本)
        self.max_rate = float(max_rate_db_per_ms) * 1000.0 / self.fs
        # 起音/释放混合宽度, 随软拐点宽度缩放
        self.blend_db = max(self.knee_db / 4.0, 0.25)
        self.engage_db = float(engage_db)
        self.c_att = math.exp(-1.0 / (self.tau_att * self.fs))
        self.c_rel = math.exp(-1.0 / (self.tau_rel * self.fs))
        self.reset()

    def reset(self):
        self._g = 0.0                              # 平滑后的增益(dB)
        self._delay = deque([0.0] * self.lookahead)  # 主路径延迟线
        self._maxq = deque()                       # 单调队列: 滑动最大检测
        self._idx = 0
        self._engaged = False
        self._triggers = 0
        self._comp_samples = 0
        self._max_gr = 0.0
        self._sum_gr = 0.0
        self._total = 0

    # ---- 静态压缩律(软拐点, C1 连续, 无斜率突变) ----
    def static_gain_db(self, level_db):
        over = level_db - self.thresh_db
        half = 0.5 * self.knee_db
        if over <= -half:
            return 0.0
        if over >= half:
            return (1.0 / self.ratio - 1.0) * over
        return ((1.0 / self.ratio - 1.0)
                * (over + half) ** 2 / (2.0 * self.knee_db))

    def knee_weight(self, level_db):
        """smoothstep 权重: 拐点以下 0, 以上 1, C1 连续."""
        k = (level_db - self.thresh_db + 0.5 * self.knee_db) / self.knee_db
        k = 0.0 if k < 0.0 else (1.0 if k > 1.0 else k)
        return k * k * (3.0 - 2.0 * k)

    # ---- 峰值保持检测: 最近 lookahead+1 个样本的滑动最大 ----
    def _push_level(self, a):
        i = self._idx
        self._idx += 1
        q = self._maxq
        while q and q[-1][1] <= a:
            q.pop()
        q.append((i, a))
        while q and q[0][0] < i - self.lookahead:
            q.popleft()
        return q[0][1]

    # ---- 逐样本递归 ----
    def _step(self, s):
        lvl = _to_db(self._push_level(abs(s)))
        target = self.static_gain_db(lvl)
        k = self.knee_weight(lvl)
        # 方向权重: 需要压扁 -> 1(起音), 需要恢复 -> 0(释放);
        # 以 blend_db 宽度平滑过渡, 避免系数硬切换造成斜率突变.
        d = 0.5 * (1.0 + math.tanh((self._g - target) / self.blend_db))
        # 软拐点曲线调制起音/释放时间常数的混合比例
        w = d * (0.25 + 0.75 * k)
        w = 0.0 if w < 0.0 else (1.0 if w > 1.0 else w)
        coef = w * self.c_att + (1.0 - w) * self.c_rel
        g = coef * self._g + (1.0 - coef) * target
        # 增益变化率限制(抑制泵浦)
        dg = g - self._g
        if dg > self.max_rate:
            g = self._g + self.max_rate
        elif dg < -self.max_rate:
            g = self._g - self.max_rate
        self._g = g
        return g

    def _update_stats(self, g):
        self._total += 1
        if g < -self.engage_db:
            if not self._engaged:
                self._triggers += 1
                self._engaged = True
            self._comp_samples += 1
            if -g > self._max_gr:
                self._max_gr = -g
            self._sum_gr += -g
        else:
            self._engaged = False

    def process(self, x, block=256):
        """返回 (out, gain, stats); out/gain 按输出时间轴对齐(含预读延迟)."""
        n = len(x)
        out = [0.0] * n
        gain = [0.0] * n
        delay = self._delay
        for start in range(0, n, block):
            blk = x[start:start + block]
            for i, s in enumerate(blk):
                g = self._step(s)
                j = start + i
                lin = 10.0 ** (g / 20.0)
                gain[j] = lin
                delay.append(s)
                out[j] = delay.popleft() * lin   # x[j - lookahead] * g
                self._update_stats(g)
        stats = {
            "triggers": self._triggers,
            "samples_in_compression": self._comp_samples,
            "total_samples": self._total,
            "max_gain_reduction_db": self._max_gr,
            "mean_gain_reduction_db":
                self._sum_gr / self._comp_samples if self._comp_samples else 0.0,
        }
        return out, gain, stats


def run(req):
    comp = LookaheadCompressor(
        req["sr"], req["thresh_db"], req["ratio"],
        knee_db=req.get("knee_db", 6.0),
        attack_ms=req.get("attack_ms", 2.0),
        release_ms=req.get("release_ms", 80.0),
        lookahead_ms=req.get("lookahead_ms", 5.0),
        max_rate_db_per_ms=req.get("max_rate_db_per_ms", 60.0))
    out, gain, stats = comp.process(req["samples"], req.get("block", 256))
    digest = hashlib.sha1(
        ",".join("{:.4f}".format(v) for v in out[:64]).encode()).hexdigest()
    return {"out": out, "gain": gain, "stats": stats, "digest": digest}


def main(argv):
    req = json.load(open(argv[1]))
    json.dump(run(req), open(argv[2], "w"))


if __name__ == "__main__":
    main(sys.argv)
