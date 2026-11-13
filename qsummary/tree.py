# -*- coding: utf-8 -*-
"""层级索引：完整二叉分区树。

结构
====
固定 ``PARTITION_COUNT=16`` 个源分区（编号 0..15），构造完整二叉树：
叶 key 为 ``"i:i+1"``，内部节点为 ``"a:b"``，全部区间半开，根为
``"0:16"``。每个节点驻留一份 :class:`~qsummary.summary.GKSummary`；
原始样本只落在磁盘上的 ``partitions_raw.json``，查询路径永不重扫。

局部重建
========
修订（revision 增大）或删除（``values=null``）时：

1. 从原始数据重建受影响的 *叶摘要*；
2. 沿父链向上，只由两个子摘要重新合并 *祖先* 摘要；
3. 其余节点完全不触碰，重建集合通过 ``recomputed`` 返回，便于核对。

查询合并
========
任意 ``[start, end)`` 先分解为互不相交、按 key 排序的规范节点覆盖
（:meth:`canonical_cover`），再折叠合并这些节点摘要。``reverse=True``
只反转折叠顺序——两种都是合法合并顺序，结果必须同样满足误差契约。

版本与快照
==========
每个摘要携带参与分区的 ``revisions`` 与内容哈希 ``summary_id``；
每个节点另存 ``node_id``。因此查询结果可以精确回答“来自哪次快照”。
不同算法契约的摘要禁止合并；整树迁移 :meth:`PartitionTree.migrate`
按稳定顺序（叶 key 升序，再逐层内部节点 key 升序）从原始数据重建。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .summary import (
    ALGORITHMS,
    GKSummary,
    QuantileError,
    SummaryVersion,
    merge_all,
)

PARTITION_COUNT = 16
RAW_FILE = "partitions_raw.json"
TREE_FILE = "tree_summary.json"
SCHEMA_VERSION = 1
QUANTILE_POINTS = (("50", 0.5), ("95", 0.95), ("99", 0.99))


class RevisionConflict(QuantileError):
    """同 revision 但内容冲突。"""


class UnknownAlgorithm(QuantileError):
    """未知算法名（CLI 以退出码 2 处理）。"""


class AlgorithmMixing(QuantileError):
    """与现有树的算法契约混用（CLI 以退出码 2 处理）。"""


class IndexOutOfRange(QuantileError):
    """分区编号不在 [0, 16)。"""


def node_key(a: int, b: int) -> str:
    return "%d:%d" % (a, b)


def split_key(key: str) -> Tuple[int, int]:
    a, b = key.split(":")
    return int(a), int(b)


def parent_key(key: str) -> Optional[str]:
    """节点 key 的父节点 key；根返回 ``None``。"""
    parent = _parent_of(*split_key(key))
    return node_key(*parent) if parent is not None else None


def _parent_of(a: int, b: int) -> Optional[Tuple[int, int]]:
    if a == 0 and b == PARTITION_COUNT:
        return None
    length = b - a
    if a % (2 * length) == 0:  # 左孩子
        return a, b + length
    return a - length, b  # 右孩子


def all_node_keys() -> List[str]:
    """自顶向下、同层 key 升序的全部 31 个节点 key（稳定顺序）。"""
    keys: List[str] = []
    length = PARTITION_COUNT
    while length >= 1:
        for a in range(0, PARTITION_COUNT, length):
            keys.append(node_key(a, a + length))
        length //= 2
    return keys


def canonical_cover(start: int, end: int) -> List[str]:
    """把半开区间 ``[start, end)`` 分解为树中规范节点（key 升序）。"""
    if not 0 <= start <= end <= PARTITION_COUNT:
        raise IndexOutOfRange("range [%d,%d) outside [0,%d)" % (start, end, PARTITION_COUNT))
    cover: List[str] = []
    cur = start
    while cur < end:
        length = 1
        # 贪心取最大的、对齐且不越界的完整子树。
        while (
            cur % (2 * length) == 0
            and cur + 2 * length <= end
            and 2 * length <= PARTITION_COUNT
        ):
            length *= 2
        cover.append(node_key(cur, cur + length))
        cur += length
    return cover


@dataclass
class PartitionRecord:
    """磁盘上的源分区记录（原始样本，不纳入摘要内存统计）。"""

    index: int
    revision: int
    values: Optional[List[int]]  # None = 墓碑（整分区删除）
    algorithm: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "revision": self.revision,
            "values": self.values,
            "algorithm": self.algorithm,
        }

    @staticmethod
    def from_dict(data: Mapping[str, object]) -> "PartitionRecord":
        return PartitionRecord(
            index=int(data["index"]),
            revision=int(data["revision"]),
            values=None if data["values"] is None else [int(v) for v in data["values"]],
            algorithm=str(data["algorithm"]),
        )


class PartitionTree:
    """驻留内存的分区摘要树，支持磁盘持久化与局部重建。"""

    def __init__(self, root: Path):
        self.root = root
        self.raw: Dict[int, PartitionRecord] = {}
        self.nodes: Dict[str, GKSummary] = {}
        self.algorithm: Optional[str] = None

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        raw_payload = {
            "schema": SCHEMA_VERSION,
            "partitions": [self.raw[i].to_dict() for i in sorted(self.raw)],
        }
        (self.root / RAW_FILE).write_text(
            json.dumps(raw_payload, sort_keys=True, indent=2) + "\n"
        )
        tree_payload = {
            "schema": SCHEMA_VERSION,
            "algorithm": self.algorithm,
            "nodes": {k: self.nodes[k].to_dict() for k in sorted(self.nodes)},
        }
        (self.root / TREE_FILE).write_text(
            json.dumps(tree_payload, sort_keys=True, indent=2) + "\n"
        )

    @staticmethod
    def load(root: Path) -> "PartitionTree":
        tree = PartitionTree(root)
        raw_path = root / RAW_FILE
        tree_path = root / TREE_FILE
        if raw_path.exists():
            raw_payload = json.loads(raw_path.read_text())
            for item in raw_payload["partitions"]:
                rec = PartitionRecord.from_dict(item)
                tree.raw[rec.index] = rec
        if tree_path.exists():
            payload = json.loads(tree_path.read_text())
            tree.algorithm = payload["algorithm"]
            for key, data in payload["nodes"].items():
                tree.nodes[key] = GKSummary.from_dict(data)
        else:
            tree._init_empty_nodes()
        return tree

    def _init_empty_nodes(self) -> None:
        # 空树也占满 31 个节点（空摘要），保证 key 集合稳定。
        if self.algorithm is None:
            return
        version = SummaryVersion.for_algorithm(self.algorithm)
        for key in all_node_keys():
            self.nodes[key] = GKSummary.empty(version)

    def _version(self) -> SummaryVersion:
        if self.algorithm is None:
            raise QuantileError("tree has no algorithm yet")
        return SummaryVersion.for_algorithm(self.algorithm)

    # ------------------------------------------------------------------ #
    # 节点构造
    # ------------------------------------------------------------------ #
    def _build_leaf(self, index: int) -> GKSummary:
        version = self._version()
        rec = self.raw.get(index)
        if rec is None or rec.values is None:
            return GKSummary.empty(version)
        return GKSummary.from_values(
            rec.values, version, revisions={str(index): rec.revision}
        )

    def _build_internal(self, key: str) -> GKSummary:
        a, b = split_key(key)
        mid = a + (b - a) // 2
        left = self.nodes[node_key(a, mid)]
        right = self.nodes[node_key(mid, b)]
        return left.merge(right)

    def _rebuild_path(self, index: int) -> List[str]:
        """重建叶 + 全部祖先，返回本次实际重建的节点 key（稳定排序）。"""
        touched = {node_key(index, index + 1)}
        self.nodes[node_key(index, index + 1)] = self._build_leaf(index)
        cur = _parent_of(index, index + 1)
        while cur is not None:
            key = node_key(*cur)
            self.nodes[key] = self._build_internal(key)
            touched.add(key)
            cur = _parent_of(*cur)
        return sorted(touched, key=lambda k: (split_key(k)[1] - split_key(k)[0], k))

    # ------------------------------------------------------------------ #
    # 批次应用
    # ------------------------------------------------------------------ #
    def apply_batch(self, partitions: Sequence[Mapping[str, object]]) -> List[str]:
        """应用一批分区记录，返回实际重建的节点 key 列表（稳定顺序）。"""
        normalized: List[PartitionRecord] = []
        for item in partitions:
            index = int(item["index"])
            if not 0 <= index < PARTITION_COUNT:
                raise IndexOutOfRange("partition index %d out of range" % index)
            revision = int(item["revision"])
            if revision <= 0:
                raise QuantileError("revision must be a positive integer")
            algorithm = str(item.get("algorithm") or "q1")
            if algorithm not in ALGORITHMS:
                raise UnknownAlgorithm("unknown algorithm: %r" % algorithm)
            values = item.get("values")
            if values is not None:
                values = [int(v) for v in values]
            normalized.append(
                PartitionRecord(index, revision, values, algorithm)
            )

        # 批次内同 index 校验。
        seen: Dict[int, PartitionRecord] = {}
        for rec in normalized:
            if rec.index in seen:
                prev = seen[rec.index]
                if prev.revision == rec.revision and prev.to_dict() != rec.to_dict():
                    raise RevisionConflict(
                        "partition %d same revision %d with conflicting payload"
                        % (rec.index, rec.revision)
                    )
            seen[rec.index] = rec

        # 与磁盘现状校验：取 revision 最大；重复不重建；同版本冲突报错。
        planned: List[PartitionRecord] = []
        for rec in normalized:
            old = self.raw.get(rec.index)
            if old is not None:
                if rec.algorithm != old.algorithm:
                    raise AlgorithmMixing(
                        "partition %d algorithm %r != stored %r; run migrate first"
                        % (rec.index, rec.algorithm, old.algorithm)
                    )
                if rec.revision < old.revision:
                    continue  # 旧快照，忽略
                if rec.revision == old.revision:
                    if rec.to_dict() == old.to_dict():
                        continue  # 重复应用，不重建
                    raise RevisionConflict(
                        "partition %d revision %d re-applied with different payload"
                        % (rec.index, rec.revision)
                    )
            if self.algorithm is None:
                self.algorithm = rec.algorithm
                self._init_empty_nodes()
            elif rec.algorithm != self.algorithm:
                raise AlgorithmMixing(
                    "algorithm %r cannot mix with tree algorithm %r"
                    % (rec.algorithm, self.algorithm)
                )
            planned.append(rec)

        recomputed: List[str] = []
        for rec in planned:
            self.raw[rec.index] = rec
            recomputed.extend(self._rebuild_path(rec.index))

        # 多叶重建时去重并保持稳定顺序（区间长度升序、key 升序）。
        ordered = sorted(set(recomputed), key=lambda k: (split_key(k)[1] - split_key(k)[0], k))
        return ordered

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def cover_summaries(self, start: int, end: int) -> List[GKSummary]:
        cover = canonical_cover(start, end)
        return [self.nodes[k] for k in cover]

    def query(self, start: int, end: int, reverse: bool = False) -> Dict[str, object]:
        cover = canonical_cover(start, end)
        summaries = [self.nodes[k] for k in cover]
        merged = merge_all(summaries, reverse=reverse)
        if merged is None or merged.is_empty():
            return {
                "count": 0,
                "quantiles": {"50": None, "95": None, "99": None},
                "revisions": {},
                "cover": cover,
                "algorithm": self.algorithm,
                "rank_error_bound": 0,
                "target_ranks": {"50": None, "95": None, "99": None},
                "participating_summary_ids": [],
                "merged_summary_id": None,
                "max_summary_items": self.max_summary_items(),
                "summary_item_cap": ALGORITHMS[self.algorithm][1] if self.algorithm else None,
                "memory_bytes": 0,
            }
        quantiles = {name: merged.quantile(p) for name, p in QUANTILE_POINTS}
        target_ranks = {name: merged.target_rank(p) for name, p in QUANTILE_POINTS}
        return {
            "count": merged.n,
            "quantiles": quantiles,
            "revisions": dict(sorted(merged.revisions.items())),
            "cover": cover,
            "algorithm": self.algorithm,
            "epsilon": merged.version.epsilon,
            "rank_error_bound": merged.rank_error_bound(),
            "target_ranks": target_ranks,
            "participating_summary_ids": [self.nodes[k].summary_id() for k in cover],
            "merged_summary_id": merged.summary_id(),
            "max_summary_items": max(self.max_summary_items(), merged.items),
            "summary_item_cap": merged.version.cap,
            "memory_bytes": merged.memory_bytes(),
        }

    def max_summary_items(self) -> int:
        """所有驻留节点中最大的加权记录条数（不含磁盘原始数据）。"""
        if not self.nodes:
            return 0
        return max(s.items for s in self.nodes.values())

    def memory_stats(self) -> Dict[str, int]:
        """驻留摘要树的内存统计。"""
        total_items = sum(s.items for s in self.nodes.values())
        return {
            "nodes": len(self.nodes),
            "total_items": total_items,
            "max_items": self.max_summary_items(),
            "memory_bytes": total_items * 160,
            "bytes_per_record": 160,
        }

    # ------------------------------------------------------------------ #
    # 迁移
    # ------------------------------------------------------------------ #
    def migrate(self, algorithm: str) -> Dict[str, object]:
        """整树迁移到另一算法：只从原始数据按稳定顺序重建全部节点。"""
        if algorithm not in ALGORITHMS:
            raise UnknownAlgorithm("unknown algorithm: %r" % algorithm)
        old_algorithm = self.algorithm
        self.algorithm = algorithm
        version = SummaryVersion.for_algorithm(algorithm)
        recomputed: List[str] = []
        # 稳定顺序：先叶 key 升序，再自底向上逐层、同层 key 升序。
        leaves = [node_key(i, i + 1) for i in range(PARTITION_COUNT)]
        for key in leaves:
            index, _ = split_key(key)
            rec = self.raw.get(index)
            if rec is not None:
                rec.algorithm = algorithm  # 原始记录跟随迁移
            self.nodes[key] = self._build_leaf(index)
            recomputed.append(key)
        length = 2
        while length <= PARTITION_COUNT:
            for a in range(0, PARTITION_COUNT, length):
                key = node_key(a, a + length)
                self.nodes[key] = self._build_internal(key)
                recomputed.append(key)
            length *= 2
        return {
            "from": old_algorithm,
            "to": algorithm,
            "recomputed": recomputed,
            "version": version.to_dict(),
        }
