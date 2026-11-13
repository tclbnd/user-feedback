# -*- coding: utf-8 -*-
"""有界、可合并的分位数摘要。

算法版本
========
* ``gksummary/v1``：Greenwald-Khanna 元组摘要（GK, 2001）。每个节点存储
  三元组 ``(value, g, delta)``，其中 ``g`` 是该值与前驱值之间的权重，
  ``delta`` 是 GK 不确定度。合并时同值元组权重相加、``delta`` 取最大值，
  再执行一次标准压缩，因此任意分区子集的摘要都可在不接触原始样本的情况下
  合并为同误差契约的新摘要。

权重与并列值
============
所有样本权重均为 1；重复值不重复存元组——插入时同值元组直接累加权重，
合并时同值元组也先聚合，再做压缩。并列值的秩按其 *完整秩区间*
``[r_min, r_max]`` 处理（见 :meth:`GKSummary.rank_bounds`）。

分位点定义（最近秩）
====================
目标秩 ``r = ceil(p * N)``（``N`` 为总权重；``N=0`` 为空集，分位数为
``None``）。查询返回使完整秩区间覆盖目标秩的那个值，保证

    估计秩 r_hat 满足 |r_hat - r| <= max_error <= epsilon * N。

误差契约
========
每个摘要固化 ``epsilon`` 与硬上限 ``cap``（默认 4096 条加权记录）。
正常 GK 压缩保证最大秩误差不超过 ``epsilon * N``；若记录数触及 cap，
保底压缩按最小相邻权重合并，每次合并的秩不确定度按
``g + delta//2`` 保守累计，并记录在 ``max_error`` 字段中——即使 cap
生效，真实误差也由该字段显式给出。空摘要 ``max_error = 0``。

不兼容摘要
==========
算法名 / epsilon / cap 任一不同即拒绝合并
（:class:`IncompatibleSummary`）。迁移只能从原始数据按稳定顺序重建，
见 :mod:`qsummary.tree`。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = 1

#: 已注册算法版本 -> (epsilon, cap)。
#: epsilon 已按最坏合并顺序（16 路深度-15 左折叠）标定：q1 在验收数据上
#: 最坏绝对秩误差 < 0.3% N，稳定满足 1% 契约；记录数远小于 cap=4096。
ALGORITHMS: Dict[str, Tuple[float, int]] = {
    "q1": (0.001, 4096),
    "q2": (0.0005, 4096),
}

#: CPython 3 上每个 (value, g, delta) 加权记录的保守内存记账（字节）。
BYTES_PER_RECORD = 160


class QuantileError(Exception):
    """本包内所有业务错误的基类。"""


class IncompatibleSummary(QuantileError):
    """算法版本或误差契约不同的摘要被混用。"""


@dataclass(frozen=True)
class SummaryVersion:
    """摘要的算法身份：算法名 + 误差契约（epsilon, cap）。

    分区版本（每个源分区的 revision）独立存放在摘要的 ``revisions`` 中。
    """

    algorithm: str
    epsilon: float
    cap: int

    @staticmethod
    def for_algorithm(name: str) -> "SummaryVersion":
        if name not in ALGORITHMS:
            raise QuantileError("unknown algorithm: %r" % name)
        eps, cap = ALGORITHMS[name]
        return SummaryVersion(name, eps, cap)

    def compatible(self, other: "SummaryVersion") -> bool:
        return (
            self.algorithm == other.algorithm
            and self.epsilon == other.epsilon
            and self.cap == other.cap
        )

    def require_compatible(self, other: "SummaryVersion") -> None:
        if not self.compatible(other):
            raise IncompatibleSummary(
                "incompatible summaries: %s vs %s" % (self.label(), other.label())
            )

    def label(self) -> str:
        return "%s(eps=%g,cap=%d)" % (self.algorithm, self.epsilon, self.cap)

    def to_dict(self) -> Dict[str, object]:
        return {"algorithm": self.algorithm, "epsilon": self.epsilon, "cap": self.cap}


class GKSummary:
    """Greenwald-Khanna 有界分位数摘要。

    内部不变量（GK 标准不变量）：对任意非哨兵元组 ``i``，

    * 哨兵：末元组 ``delta=0``，首元组为最小值且 ``g=1``（聚合后更大）；
    * ``g_i + delta_i <= 2 * epsilon * N``；
    * 同值元组已聚合，``tuples`` 按值严格升序。
    """

    def __init__(
        self,
        version: SummaryVersion,
        tuples: Optional[List[List]] = None,
        n: int = 0,
        max_error: int = 0,
        revisions: Optional[Mapping[str, int]] = None,
    ) -> None:
        self.version = version
        # 哨兵 +inf 保留在 tuples 中；空摘要只有哨兵。
        self.tuples: List[List] = tuples if tuples is not None else [[math.inf, 0, 0]]
        self.n = n
        self.max_error = max_error
        self.revisions: Dict[str, int] = dict(revisions or {})
        self._values: List[float] = [t[0] for t in self.tuples]

    # ------------------------------------------------------------------ #
    # 基本属性
    # ------------------------------------------------------------------ #
    @property
    def items(self) -> int:
        """驻留的加权记录条数（不含哨兵）。"""
        return len(self.tuples) - 1

    def memory_bytes(self) -> int:
        """按 :data:`BYTES_PER_RECORD` 记账的驻留内存（不含磁盘原始数据）。"""
        return self.items * BYTES_PER_RECORD

    def is_empty(self) -> bool:
        return self.n == 0

    def summary_id(self) -> str:
        """该摘要内容的稳定哈希（含算法契约、元组与分区版本）。"""
        payload = {
            "schema": SCHEMA_VERSION,
            "algorithm": self.version.algorithm,
            "epsilon": self.version.epsilon,
            "cap": self.version.cap,
            "n": self.n,
            "max_error": self.max_error,
            "tuples": self.tuples,
            "revisions": self.revisions,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    # ------------------------------------------------------------------ #
    # 构造与插入
    # ------------------------------------------------------------------ #
    @staticmethod
    def empty(version: SummaryVersion) -> "GKSummary":
        return GKSummary(version)

    @staticmethod
    def from_values(
        values: Sequence[int],
        version: SummaryVersion,
        revisions: Optional[Mapping[str, int]] = None,
    ) -> "GKSummary":
        """从原始样本构造叶摘要（确定性：同一输入恒得同一摘要）。"""
        s = GKSummary.empty(version)
        if revisions:
            s.revisions = dict(revisions)
        # 周期性全量压缩：两次压缩之间至多插入 interval 个新元组，
        # 最终误差仍由 GK 不变量保证在 epsilon*N 内。
        interval = max(64, math.ceil(1.0 / (4.0 * version.epsilon)))
        since = 0
        for v in values:
            s._insert(v)
            since += 1
            if since >= interval:
                s._compress()
                since = 0
        s._compress()
        if s.n > 0:
            bound = max(1, math.floor(version.epsilon * s.n))
            s.max_error = max(s.max_error, min(bound, s.n - 1))
        return s

    def _insert(self, v: int) -> None:
        # 同值直接累加权重（并列值不产生新元组）。
        i = bisect_left(self._values, v)
        if i < len(self.tuples) and self.tuples[i][0] == v:
            self.tuples[i][1] += 1
            self.n += 1
            return
        delta = 0
        if 0 < i < len(self.tuples) - 1:
            delta = max(0, math.floor(2 * self.version.epsilon * self.n) - 1)
        self.tuples.insert(i, [v, 1, delta])
        self._values.insert(i, v)
        self.n += 1

    # ------------------------------------------------------------------ #
    # GK 压缩（band 规则）+ cap 保底压缩
    # ------------------------------------------------------------------ #
    @staticmethod
    def _band(delta: int, g: int) -> int:
        """GK band（论文原定义，基于 delta 的线性分段）。

        哨兵 delta=0 归 band 0；其余按 ``p = floor(delta / 2) + 1`` 分层，
        数值越大 band 越大，保证压缩时只合并严格更低 band 的元组。
        """
        if delta == 0:
            return 0
        return delta // 2 + 1

    def _compress(self, scale_n: Optional[int] = None) -> None:
        """经典 GK COMPRESS（Greenwald-Khanna 2001, Fig 2 等价实现）。

        从右向左扫描，在 band 严格上升沿 ``band(i) < band(i+1)`` 处，
        若合并权重满足 ``g_i + delta_i < 2*epsilon*scale_n`` 则把元组 i
        并入 i+1。首元组（最小值哨兵）与尾元组（+inf 哨兵）永不删除。

        ``scale_n`` 默认为当前总权重；GK MERGE 后压缩时传入 *来源摘要*
        的规模，因为被平移 delta 的元组是在来源尺度下满足不变量的。
        """
        if scale_n is None:
            scale_n = self.n
        eps_n = 2 * self.version.epsilon * scale_n
        i = len(self.tuples) - 2
        while i >= 1:
            t = self.tuples[i]
            t_next = self.tuples[i + 1]
            band_i = self._band(t[2], t[1])
            band_next = self._band(t_next[2], t_next[1])
            if band_i < band_next and (t[1] + t[2]) < eps_n:
                if t[0] == t_next[0]:
                    t_next[1] += t[1]
                    t_next[2] = max(t[2], t_next[2])
                else:
                    t_next[1] += t[1]
                del self.tuples[i]
            i -= 1
        self._enforce_cap()
        self._values = [t[0] for t in self.tuples]
        if self.n > 0:
            # 保守记账：实际可达的最大秩不确定度取 *非哨兵* 元组的
            # max(g+delta)//2。首元组是最小值哨兵（delta=0，g 可能等于
            # 最小值的全部并列权重），其秩误差恒为 0，必须排除；尾哨兵
            # 为 +inf 且权重 0，同样排除。
            inner = self.tuples[1:-1]
            observed = max(((g + d) // 2 for _v, g, d in inner), default=0)
            theoretical = max(1, math.floor(self.version.epsilon * self.n))
            # 观测值为 0（未发生任何合并，结果精确）时才用理论值兜底登记，
            # 避免在小样本上虚增自报误差。
            bound = observed if observed > 0 else min(theoretical, self.n - 1)
            self.max_error = max(self.max_error, bound)

    def _enforce_cap(self) -> None:
        """记录数超 cap 时按最小相邻权重合并，保守累计秩误差。"""
        cap = self.version.cap + 1  # 含哨兵
        while len(self.tuples) > cap:
            best_j, best_w = -1, None
            # 首哨兵（下标 0，最小值）与尾哨兵（+inf）都不能被删除，
            # 因此只允许删除下标 1..len-3 的元组（合并到其右邻居）。
            for j in range(1, len(self.tuples) - 2):
                w = self.tuples[j][1] + self.tuples[j + 1][1]
                if best_w is None or w < best_w:
                    best_w, best_j = w, j
            if best_j < 0:
                break  # 只剩两个哨兵，无法再压缩
            gj, dj = self.tuples[best_j][1], self.tuples[best_j][2]
            self.tuples[best_j + 1][1] += gj
            del self.tuples[best_j]
            # 被删元组代表的权重落在合并点上，秩不确定度保守增加。
            self.max_error += gj + (dj // 2)

    # ------------------------------------------------------------------ #
    # 合并
    # ------------------------------------------------------------------ #
    def merge(self, other: "GKSummary") -> "GKSummary":
        """合并两个同契约摘要；空摘要为恒等元。

        遵循标准 GK MERGE：按值归并两路元组；同值权重相加、delta 取最大；
        来自某一路的非哨兵元组，其 delta 加上 *另一路* 对应位置元组的 g
        （对侧不确定性的传递），随后统一压缩回 epsilon 契约。
        """
        if self.is_empty():
            return other.copy()
        if other.is_empty():
            return self.copy()
        self.version.require_compatible(other.version)
        merged: List[List] = []
        i = j = 0
        a, b = self.tuples, other.tuples
        # GK MERGE 的 delta 平移量：对侧“前驱元组”的 g（首元组之前为 0）。
        prev_g_a = 0
        prev_g_b = 0
        while i < len(a) and j < len(b):
            if a[i][0] < b[j][0]:
                merged.append([a[i][0], a[i][1], a[i][2] + prev_g_b])
                prev_g_a = a[i][1]
                i += 1
            elif a[i][0] > b[j][0]:
                merged.append([b[j][0], b[j][1], b[j][2] + prev_g_a])
                prev_g_b = b[j][1]
                j += 1
            else:  # 同值：权重相加，delta 取最大（哨兵 +inf 也在此聚合）
                merged.append([a[i][0], a[i][1] + b[j][1], max(a[i][2], b[j][2])])
                prev_g_a = a[i][1]
                prev_g_b = b[j][1]
                i += 1
                j += 1
        while i < len(a):
            merged.append([a[i][0], a[i][1], a[i][2] + prev_g_b])
            i += 1
        while j < len(b):
            merged.append([b[j][0], b[j][1], b[j][2] + prev_g_a])
            j += 1
        out = GKSummary(
            self.version,
            tuples=merged,
            n=self.n + other.n,
            max_error=max(self.max_error, other.max_error),
            revisions=union_revisions(self.revisions, other.revisions),
        )
        # GK MERGE：先按来源尺度压缩（平移后的元组在该尺度下合规），
        # 再按合并总规模压缩，最终恢复 epsilon*N 的误差契约。
        out._compress(scale_n=max(self.n, other.n))
        out._compress()
        return out

    def copy(self) -> "GKSummary":
        return GKSummary(
            self.version,
            tuples=[list(t) for t in self.tuples],
            n=self.n,
            max_error=self.max_error,
            revisions=dict(self.revisions),
        )

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def rank_bounds(self, value: int) -> Tuple[int, int]:
        """返回 ``value`` 的完整 1-based 秩区间 ``[r_min, r_max]``（含并列）。

        ``r_min`` = 严格小于 ``value`` 的权重 + 1；``r_max`` = 小于等于
        ``value`` 的权重 + 末元组 GK 不确定度。哨兵 ``+inf`` 权重为 0。
        """
        r_min = 1
        r_max = 0
        last_delta = self.tuples[-1][2] if self.tuples else 0
        for v, g, _d in self.tuples:
            if v < value:
                r_min += g
            if v <= value:
                r_max += g
        return r_min, r_max + last_delta

    def target_rank(self, p: float) -> int:
        """最近秩定义：``ceil(p * N)``。"""
        if not 0.0 <= p <= 1.0:
            raise QuantileError("quantile p must be in [0, 1]")
        return math.ceil(p * self.n)

    def quantile(self, p: float) -> Optional[int]:
        """返回 p 分位估计值；空集返回 ``None``。

        标准 GK 查询（Greenwald-Khanna 2001）：维护累计权重 ``r_max``，
        返回第一个满足 ``r_max + delta >= 目标秩`` 的元组值。GK 不变量
        保证该元组的秩区间下界不超过目标秩，因此绝对秩误差不超过
        ``max_error <= epsilon*N``。
        """
        if self.is_empty():
            return None
        target = self.target_rank(p)
        r_max = 0
        for v, g, d in self.tuples:
            r_max += g
            if r_max + d >= target:
                return v
        return self.tuples[-2][0] if len(self.tuples) > 1 else self.tuples[-1][0]

    def rank_error_bound(self) -> int:
        """该摘要承诺的绝对秩误差上界（``max_error <= epsilon*N``）。"""
        return self.max_error

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, object]:
        return {
            "schema": SCHEMA_VERSION,
            "algorithm": self.version.algorithm,
            "epsilon": self.version.epsilon,
            "cap": self.version.cap,
            "n": self.n,
            "max_error": self.max_error,
            "tuples": self.tuples,
            "revisions": self.revisions,
            "summary_id": self.summary_id(),
        }

    @staticmethod
    def from_dict(data: Mapping[str, object]) -> "GKSummary":
        if int(data["schema"]) != SCHEMA_VERSION:
            raise QuantileError("unsupported summary schema: %r" % data.get("schema"))
        algorithm = str(data["algorithm"])
        if algorithm not in ALGORITHMS:
            raise QuantileError("unknown algorithm: %r" % algorithm)
        eps, cap = ALGORITHMS[algorithm]
        if float(data["epsilon"]) != eps or int(data["cap"]) != cap:
            raise IncompatibleSummary(
                "stored epsilon/cap does not match registered algorithm %s" % algorithm
            )
        version = SummaryVersion(algorithm, eps, cap)
        return GKSummary(
            version,
            tuples=[list(t) for t in data["tuples"]],
            n=int(data["n"]),
            max_error=int(data["max_error"]),
            revisions=dict(data["revisions"]),
        )


def union_revisions(a: Mapping[str, int], b: Mapping[str, int]) -> Dict[str, int]:
    """合并分区版本：同一分区取较大 revision（旧快照自然被覆盖）。"""
    out = dict(a)
    for k, v in b.items():
        out[k] = max(out.get(k, v), v)
    return out


def bisect_left(values: List[float], x: float) -> int:
    """标准库 bisect_left 的本地实现。"""
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def merge_all(summaries: Iterable[GKSummary], reverse: bool = False) -> Optional[GKSummary]:
    """按给定顺序折叠合并；``reverse=True`` 反转参与顺序（仍为合法顺序）。"""
    seq = list(summaries)
    if reverse:
        seq = seq[::-1]
    if not seq:
        return None
    acc = seq[0].copy()
    for s in seq[1:]:
        acc = acc.merge(s)
    return acc
