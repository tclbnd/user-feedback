# -*- coding: utf-8 -*-
"""命令行入口：apply / query / migrate / stats。

退出码约定：

* 0 成功；
* 2 未知算法或算法混用（README 契约）；
* 1 其他业务错误（revision 冲突、越界、I/O 等）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Sequence

from .summary import ALGORITHMS, QuantileError
from .tree import (
    PARTITION_COUNT,
    AlgorithmMixing,
    PartitionTree,
    UnknownAlgorithm,
)

INT32_MIN = -(2 ** 31)
INT32_MAX = 2 ** 31 - 1


def _print(payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2))


def _validate_values(partitions: Sequence[dict]) -> None:
    for item in partitions:
        values = item.get("values")
        if values is None:
            continue
        for v in values:
            iv = int(v)
            if not INT32_MIN <= iv <= INT32_MAX:
                raise QuantileError(
                    "partition %d contains value outside signed 32-bit range: %d"
                    % (int(item["index"]), iv)
                )


def cmd_apply(root: Path, input_path: Path) -> int:
    payload = json.loads(input_path.read_text())
    partitions = payload["partitions"]
    _validate_values(partitions)
    tree = PartitionTree.load(root)
    recomputed = tree.apply_batch(partitions)
    tree.save()
    _print(
        {
            "recomputed": recomputed,
            "algorithm": tree.algorithm,
            "memory_stats": tree.memory_stats(),
            "max_summary_items": tree.max_summary_items(),
        }
    )
    return 0


def cmd_query(root: Path, start: int, end: int, reverse: bool) -> int:
    tree = PartitionTree.load(root)
    _print(tree.query(start, end, reverse=reverse))
    return 0


def cmd_migrate(root: Path, algorithm: str) -> int:
    if algorithm not in ALGORITHMS:
        raise UnknownAlgorithm("unknown algorithm: %r" % algorithm)
    tree = PartitionTree.load(root)
    result = tree.migrate(algorithm)
    tree.save()
    result["memory_stats"] = tree.memory_stats()
    _print(result)
    return 0


def cmd_stats(root: Path) -> int:
    tree = PartitionTree.load(root)
    _print(
        {
            "algorithm": tree.algorithm,
            "memory_stats": tree.memory_stats(),
            "max_summary_items": tree.max_summary_items(),
            "partitions": {
                str(i): tree.raw[i].to_dict()
                for i in sorted(tree.raw)
            },
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantiles")
    parser.add_argument("root", type=Path)
    sub = parser.add_subparsers(dest="op", required=True)

    apply_p = sub.add_parser("apply", help="应用一批分区修订/删除")
    apply_p.add_argument("input", type=Path)

    query_p = sub.add_parser("query", help="按半开区间 [start,end) 查询")
    query_p.add_argument("--start", type=int, required=True)
    query_p.add_argument("--end", type=int, required=True)
    query_p.add_argument("--reverse", action="store_true", help="反转合法合并顺序")

    migrate_p = sub.add_parser("migrate", help="整树迁移算法（稳定顺序重建）")
    migrate_p.add_argument("--algorithm", required=True, choices=sorted(ALGORITHMS))

    sub.add_parser("stats", help="输出内存与版本统计")
    return parser


def main(argv: List[str] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.op == "apply":
            return cmd_apply(args.root, args.input)
        if args.op == "query":
            if not 0 <= args.start <= args.end <= PARTITION_COUNT:
                raise QuantileError(
                    "range [%d,%d) outside [0,%d)" % (args.start, args.end, PARTITION_COUNT)
                )
            return cmd_query(args.root, args.start, args.end, args.reverse)
        if args.op == "migrate":
            return cmd_migrate(args.root, args.algorithm)
        if args.op == "stats":
            return cmd_stats(args.root)
    except (UnknownAlgorithm, AlgorithmMixing) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except QuantileError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
