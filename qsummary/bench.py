# -*- coding: utf-8 -*-
"""固定种子的验收场景与数据集（测试与报告工具共享）。

三类输入，16 个分区，分区样本量刻意不均：

* ``uniform``：均匀整数；
* ``longtail``：Pareto 长尾；
* ``duplicates``：仅 12 个不同取值，制造大量并列。
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Mapping, Sequence

from .reference import QUANTILE_POINTS, absolute_rank_error, exact_query
from .summary import GKSummary, SummaryVersion, merge_all
from .tree import PARTITION_COUNT, PartitionTree

SEED = 20261114
CAP = 4096
RANK_TOLERANCE = 0.01
SIZES = [300, 900, 150, 2600, 480, 1200, 90, 1800,
         640, 2100, 130, 750, 3200, 220, 540, 1100]


def build_partitions(kind: str, seed: int = SEED) -> Dict[int, List[int]]:
    """生成样本量不均的 16 分区数据集；同种子重复运行完全一致。"""
    rnd = random.Random(seed + hash_kind(kind))
    out: Dict[int, List[int]] = {}
    for index, size in enumerate(SIZES):
        if kind == "uniform":
            out[index] = [rnd.randint(-100_000, 100_000) for _ in range(size)]
        elif kind == "longtail":
            out[index] = [
                min(2 ** 31 - 1, int(rnd.paretovariate(2.2))) for _ in range(size)
            ]
        elif kind == "duplicates":
            out[index] = [rnd.randint(0, 11) for _ in range(size)]
        else:
            raise ValueError("unknown dataset kind: %r" % kind)
    return out


def hash_kind(kind: str) -> int:
    return int.from_bytes(kind.encode("utf-8"), "little") % 10_000


def apply_initial(tree: PartitionTree, partitions: Mapping[int, Sequence[int]],
                  algorithm: str = "q1") -> List[str]:
    batch = [
        {"index": i, "revision": 1, "values": list(partitions[i]),
         "algorithm": algorithm}
        for i in sorted(partitions)
    ]
    return tree.apply_batch(batch)


def _check_quantiles(summary: GKSummary, sorted_values: Sequence[int]) -> List[Dict[str, object]]:
    n = len(sorted_values)
    rows = []
    for name, p in QUANTILE_POINTS:
        target = math.ceil(p * n)
        est = summary.quantile(p)
        err = absolute_rank_error(sorted_values, est, target)
        exact = sorted_values[target - 1]
        rows.append({
            "quantile": name,
            "target_rank": target,
            "exact_value": exact,
            "estimated_value": est,
            "absolute_rank_error": err,
            "tolerance": RANK_TOLERANCE * n,
            "within_1_percent": err <= RANK_TOLERANCE * n,
            "summary_bound": summary.rank_error_bound(),
        })
    return rows


def accuracy_scenario(kind: str, algorithm: str = "q1") -> Dict[str, object]:
    """全范围查询，对三种合法合并顺序分别核对。"""
    partitions = build_partitions(kind)
    version = SummaryVersion.for_algorithm(algorithm)
    leaf = {
        i: GKSummary.from_values(partitions[i], version, revisions={str(i): 1})
        for i in partitions
    }
    ordered = [leaf[i] for i in range(PARTITION_COUNT)]
    reference = exact_query(partitions, 0, PARTITION_COUNT)
    sd = reference["sorted"]
    orders = {
        "ascending": ordered,
        "descending": list(reversed(ordered)),
        "strided": [ordered[i] for i in (0, 3, 6, 9, 12, 15, 2, 5, 8, 11, 14, 1, 4, 7, 10, 13)],
    }
    order_results = {}
    for label, seq in orders.items():
        merged = merge_all(seq)
        order_results[label] = {
            "checks": _check_quantiles(merged, sd),
            "count": merged.n,
            "summary_id": merged.summary_id(),
            "items": merged.items,
        }
    return {
        "kind": kind,
        "algorithm": algorithm,
        "n": len(sd),
        "orders": order_results,
        "reference_quantiles": reference["quantiles"],
    }


def tree_accuracy_scenario(kind: str, reverse: bool = False) -> Dict[str, object]:
    """走层级索引查询路径，并与精确排序核对。"""
    partitions = build_partitions(kind)
    tree = PartitionTree(root=None)  # type: ignore[arg-type]
    apply_initial(tree, partitions)
    result = tree.query(0, PARTITION_COUNT, reverse=reverse)
    reference = exact_query(partitions, 0, PARTITION_COUNT)
    checks = []
    for name, p in QUANTILE_POINTS:
        target = math.ceil(p * len(reference["sorted"]))
        err = absolute_rank_error(reference["sorted"], result["quantiles"][name], target)
        checks.append({
            "quantile": name,
            "estimated_value": result["quantiles"][name],
            "exact_value": reference["quantiles"][name],
            "absolute_rank_error": err,
            "within_1_percent": err <= RANK_TOLERANCE * reference["count"],
        })
    return {
        "kind": kind,
        "reverse": reverse,
        "query": result,
        "checks": checks,
        "reference_count": reference["count"],
        "memory_stats": tree.memory_stats(),
    }


def correction_scenario(kind: str = "uniform") -> Dict[str, object]:
    """回放稀疏更正与整分区删除，重算受影响范围并核对重建集合。"""
    partitions = build_partitions(kind)
    tree = PartitionTree(root=None)  # type: ignore[arg-type]
    apply_initial(tree, partitions)

    expected_path = lambda index: _ancestor_keys(index)
    # 1) 稀疏更正：分区 4 revision 2。
    corrected_values = list(reversed(partitions[4]))[:300] + [777_777] * 120
    recomputed_1 = tree.apply_batch([
        {"index": 4, "revision": 2, "values": corrected_values, "algorithm": "q1"}
    ])
    partitions[4] = corrected_values
    expected_1 = expected_path(4)

    # 2) 整分区删除：分区 11 values=null。
    recomputed_2 = tree.apply_batch([
        {"index": 11, "revision": 2, "values": None, "algorithm": "q1"}
    ])
    deleted = partitions.pop(11)
    expected_2 = expected_path(11)

    # 3) 旧 revision 重放必须被忽略，不触发重建。
    recomputed_3 = tree.apply_batch([
        {"index": 4, "revision": 1, "values": [1, 2, 3], "algorithm": "q1"}
    ])

    reference = exact_query(partitions, 0, PARTITION_COUNT)
    result = tree.query(0, PARTITION_COUNT)
    checks = []
    for name, p in QUANTILE_POINTS:
        target = math.ceil(p * reference["count"])
        err = absolute_rank_error(reference["sorted"], result["quantiles"][name], target)
        checks.append({
            "quantile": name,
            "absolute_rank_error": err,
            "within_1_percent": err <= RANK_TOLERANCE * reference["count"],
        })
    live_sizes = {str(i): len(v) for i, v in partitions.items()}
    return {
        "recomputed_correction": recomputed_1,
        "expected_correction": expected_1,
        "recomputed_deletion": recomputed_2,
        "expected_deletion": expected_2,
        "recomputed_stale": recomputed_3,
        "query_count": result["count"],
        "exact_count": reference["count"],
        "count_matches": result["count"] == reference["count"],
        "revisions": result["revisions"],
        "expected_live_sizes": live_sizes,
        "deleted_partition": 11,
        "deleted_size": len(deleted),
        "checks": checks,
        "participating_summary_ids": result["participating_summary_ids"],
        "memory_stats": tree.memory_stats(),
        "max_summary_items": tree.max_summary_items(),
        "cap": CAP,
        "tree": tree,
        "partitions": partitions,
    }


def determinism_scenario(kind: str = "duplicates") -> Dict[str, object]:
    """固定种子两次独立构建，逐字节比较全部节点摘要哈希。"""
    partitions = build_partitions(kind)
    tree_a = PartitionTree(root=None)  # type: ignore[arg-type]
    tree_b = PartitionTree(root=None)  # type: ignore[arg-type]
    apply_initial(tree_a, partitions)
    apply_initial(tree_b, partitions)
    ids_a = {k: s.summary_id() for k, s in tree_a.nodes.items()}
    ids_b = {k: s.summary_id() for k, s in tree_b.nodes.items()}
    query_a = tree_a.query(2, 14)
    query_b = tree_b.query(2, 14)
    return {
        "node_ids_equal": ids_a == ids_b,
        "query_ids_equal": query_a["merged_summary_id"] == query_b["merged_summary_id"],
        # 不同合法合并顺序允许产生不同的摘要表示（哈希不同），但样本数、
        # 分位估计与误差契约必须一致——这正是“顺序不同、结果等价”。
        "order_changes_representation": (
            tree_a.query(2, 14)["merged_summary_id"]
            != tree_a.query(2, 14, reverse=True)["merged_summary_id"]
        ),
        "reverse_count_equal": (
            tree_a.query(2, 14)["count"]
            == tree_a.query(2, 14, reverse=True)["count"]
        ),
        "reverse_quantiles_equal": (
            tree_a.query(2, 14)["quantiles"]
            == tree_a.query(2, 14, reverse=True)["quantiles"]
        ),
    }


def migration_scenario() -> Dict[str, object]:
    """q1 -> q2 稳定顺序迁移，并验证混用拒绝。"""
    partitions = build_partitions("uniform")
    tree = PartitionTree(root=None)  # type: ignore[arg-type]
    apply_initial(tree, partitions, algorithm="q1")
    ids_before = {k: s.summary_id() for k, s in tree.nodes.items()}
    migration = tree.migrate("q2")
    ids_after = {k: s.summary_id() for k, s in tree.nodes.items()}
    all_keys = list(ids_before)
    # 稳定顺序：叶先、按 key 升序；之后自底向上。
    stable_order = migration["recomputed"]
    mixing_rejected = False
    try:
        tree.apply_batch([
            {"index": 0, "revision": 3, "values": [1], "algorithm": "q1"}
        ])
    except Exception:
        mixing_rejected = True
    # q2 下误差契约更严格。
    q2_summary = tree.nodes["0:16"]
    return {
        "all_nodes_rebuilt": set(stable_order) == set(all_keys) and len(stable_order) == len(all_keys),
        "leaf_first_sorted": stable_order[:PARTITION_COUNT]
        == ["%d:%d" % (i, i + 1) for i in range(PARTITION_COUNT)],
        "ids_changed": ids_before != ids_after,
        "algorithm": tree.algorithm,
        "q2_epsilon": q2_summary.version.epsilon,
        "mixing_rejected": mixing_rejected,
        "tree": tree,
    }


def memory_scenario() -> Dict[str, object]:
    """内存上界核对：每条节点 <= 4096 条记录，且记账内存可复算。"""
    report = {}
    for kind in ("uniform", "longtail", "duplicates"):
        partitions = build_partitions(kind)
        tree = PartitionTree(root=None)  # type: ignore[arg-type]
        apply_initial(tree, partitions)
        stats = tree.memory_stats()
        per_node = {k: s.items for k, s in tree.nodes.items()}
        report[kind] = {
            "max_items": stats["max_items"],
            "cap": CAP,
            "within_cap": stats["max_items"] <= CAP,
            "memory_bytes": stats["memory_bytes"],
            "recomputed_bytes": stats["total_items"] * stats["bytes_per_record"],
            "per_node_items": per_node,
        }
    return report


def _ancestor_keys(index: int) -> List[str]:
    from .tree import _parent_of, node_key, split_key
    keys = [node_key(index, index + 1)]
    cur = _parent_of(index, index + 1)
    while cur is not None:
        keys.append(node_key(*cur))
        cur = _parent_of(*cur)
    return sorted(keys, key=lambda k: (split_key(k)[1] - split_key(k)[0], k))
