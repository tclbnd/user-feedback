# -*- coding: utf-8 -*-
"""独立验收报告：精确排序核对、合并顺序、更正/删除、内存、确定性与迁移。

用法：
    python3 tools/run_acceptance.py            # 人类可读报告，退出码反映成败
    python3 tools/run_acceptance.py --json     # 机器可读 JSON
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qsummary.bench import (  # noqa: E402
    CAP,
    accuracy_scenario,
    correction_scenario,
    determinism_scenario,
    memory_scenario,
    migration_scenario,
    tree_accuracy_scenario,
)

DATA_KINDS = ("uniform", "longtail", "duplicates")


def collect() -> dict:
    started = time.perf_counter()
    report = {"accuracy": [], "tree_accuracy": [], "correction": None,
              "determinism": None, "migration": None, "memory": None}
    for kind in DATA_KINDS:
        report["accuracy"].append(accuracy_scenario(kind))
        report["tree_accuracy"].append(tree_accuracy_scenario(kind))
    report["correction"] = correction_scenario("uniform")
    report["determinism"] = determinism_scenario()
    report["migration"] = migration_scenario()
    report["memory"] = memory_scenario()
    report["elapsed_seconds"] = round(time.perf_counter() - started, 4)
    return report


def all_pass(report: dict) -> bool:
    ok = True
    for block in report["accuracy"]:
        for detail in block["orders"].values():
            ok &= all(row["within_1_percent"] for row in detail["checks"])
            ok &= detail["items"] <= CAP
    for block in report["tree_accuracy"]:
        ok &= all(row["within_1_percent"] for row in block["checks"])
    c = report["correction"]
    ok &= c["recomputed_correction"] == c["expected_correction"]
    ok &= c["recomputed_deletion"] == c["expected_deletion"]
    ok &= c["recomputed_stale"] == []
    ok &= c["count_matches"]
    ok &= all(row["within_1_percent"] for row in c["checks"])
    ok &= c["max_summary_items"] <= c["cap"]
    ok &= report["determinism"]["node_ids_equal"]
    ok &= report["determinism"]["query_ids_equal"]
    ok &= report["migration"]["all_nodes_rebuilt"]
    ok &= report["migration"]["mixing_rejected"]
    ok &= all(d["within_cap"] for d in report["memory"].values())
    return ok


def print_human(report: dict) -> None:
    print("== 1. 精确排序核对（P50/P95/P99，三种合法合并顺序） ==")
    for block in report["accuracy"]:
        print("[%s] N=%d" % (block["kind"], block["n"]))
        for order, detail in block["orders"].items():
            for row in detail["checks"]:
                print(
                    "  %-9s %s: exact=%d est=%d 秩误差=%d 容忍=%.1f 契约上界=%d %s"
                    % (order, row["quantile"], row["exact_value"],
                       row["estimated_value"], row["absolute_rank_error"],
                       row["tolerance"], row["summary_bound"],
                       "OK" if row["within_1_percent"] else "FAIL")
                )
    print("\n== 2. 层级索引查询（含反向合并顺序） ==")
    for block in report["tree_accuracy"]:
        print("[%s reverse=%s] count=%d cover=%d 节点"
              % (block["kind"], block["reverse"], block["query"]["count"],
                 len(block["query"]["cover"])))
        for row in block["checks"]:
            print("  %s: exact=%s est=%s 秩误差=%d %s"
                  % (row["quantile"], row["exact_value"], row["estimated_value"],
                     row["absolute_rank_error"],
                     "OK" if row["within_1_percent"] else "FAIL"))
    c = report["correction"]
    print("\n== 3. 稀疏更正与整分区删除 ==")
    print("  更正重建集合 == 预期:", c["recomputed_correction"] == c["expected_correction"])
    print("  删除重建集合 == 预期:", c["recomputed_deletion"] == c["expected_deletion"])
    print("  旧 revision 重放重建数:", c["recomputed_stale"])
    print("  查询样本数 == 精确重算:", c["query_count"], "==", c["exact_count"],
          c["count_matches"])
    print("  参与分区版本:", json.dumps(c["revisions"], sort_keys=True))
    print("  被删分区 %d 不在版本表:" % c["deleted_partition"],
          str(c["deleted_partition"]) not in c["revisions"])
    print("  最大摘要记录数 %d <= cap %d" % (c["max_summary_items"], c["cap"]))
    print("\n== 4. 固定种子确定性 ==")
    print(" ", json.dumps(report["determinism"], sort_keys=True))
    print("\n== 5. 稳定顺序迁移与混用拒绝 ==")
    m = report["migration"]
    print("  全部 31 节点重建:", m["all_nodes_rebuilt"],
          "| 叶优先且升序:", m["leaf_first_sorted"],
          "| 混用被拒绝:", m["mixing_rejected"])
    print("\n== 6. 内存统计（每节点 <= 4096 条记录） ==")
    for kind, d in report["memory"].items():
        print("  %-10s max_items=%d memory=%dB %s"
              % (kind, d["max_items"], d["memory_bytes"],
                 "OK" if d["within_cap"] else "FAIL"))
    print("\n耗时 %.2fs" % report["elapsed_seconds"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = collect()
    # 移除不可 JSON 序列化的现场对象。
    report["correction"].pop("tree", None)
    report["correction"].pop("partitions", None)
    report["migration"].pop("tree", None)
    if args.json:
        json.dump(report, sys.stdout, sort_keys=True, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print_human(report)
    return 0 if all_pass(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
