# -*- coding: utf-8 -*-
"""精确参照实现：全量排序，用于验收核对（不参与线上查询路径）。"""
from __future__ import annotations

import math
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

QUANTILE_POINTS = (("50", 0.5), ("95", 0.95), ("99", 0.99))


def nearest_rank(sorted_values: Sequence[int], p: float) -> Optional[int]:
    """最近秩分位：``sorted_values[ceil(p*N)-1]``；空集为 ``None``。"""
    n = len(sorted_values)
    if n == 0:
        return None
    return sorted_values[math.ceil(p * n) - 1]


def exact_quantiles(values: Sequence[int]) -> Dict[str, Optional[int]]:
    s = sorted(values)
    return {name: nearest_rank(s, p) for name, p in QUANTILE_POINTS}


def rank_interval(sorted_values: Sequence[int], value: int) -> Tuple[int, int]:
    """并列值在精确排序中的完整 1-based 秩区间 ``[r_min, r_max]``。"""
    lo = hi = 0
    for i, v in enumerate(sorted_values):
        if v < value:
            lo = i + 1
        if v <= value:
            hi = i + 1
    # 第一个 <= value 的位置 +1。
    r_min = 0
    for i, v in enumerate(sorted_values):
        if v == value:
            r_min = i + 1
            break
    return r_min, hi


def target_ranks(n: int) -> Dict[str, Optional[int]]:
    if n == 0:
        return {"50": None, "95": None, "99": None}
    return {name: math.ceil(p * n) for name, p in QUANTILE_POINTS}


def absolute_rank_error(
    sorted_values: Sequence[int], estimate: Optional[int], target_rank: int
) -> int:
    """按并列值完整秩区间计算的绝对秩误差。

    估计值 ``x`` 的真实秩区间为 ``[r_min(x), r_max(x)]``；目标秩落在该
    区间内则误差为 0，否则取到区间端点的最近距离。
    """
    if estimate is None:
        raise ValueError("estimate required")
    r_min, r_max = rank_interval(sorted_values, estimate)
    if r_min <= target_rank <= r_max:
        return 0
    if target_rank < r_min:
        return r_min - target_rank
    return target_rank - r_max


def exact_query(
    partitions: Mapping[int, Sequence[int]], start: int, end: int
) -> Dict[str, object]:
    """对 ``[start,end)`` 内非空分区做精确排序参照查询。"""
    values: List[int] = []
    live: Dict[str, int] = {}
    for index in range(start, end):
        part = partitions.get(index)
        if part:
            values.extend(part)
            live[str(index)] = len(part)
    s = sorted(values)
    return {
        "count": len(s),
        "quantiles": exact_quantiles(s),
        "target_ranks": target_ranks(len(s)),
        "sorted": s,
        "live_partition_sizes": live,
    }
