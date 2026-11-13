"""无倾斜高基数夹具：大量几乎唯一的键，迫使走再分区递归与 BNL 兜底。

每个键最多出现 1-2 次，不存在多对多放大；数据整体远超内存预算，分区后
单个分区仍可能超预算，此时热点选择为空，引擎必须通过递归再分区细分，
或在递归深度用尽后用有界 BNL 完成，且结果与 SQLite 完全一致。
"""
import csv
import sys
from pathlib import Path


def write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["key_a", "key_b", "value"])
        w.writerows(rows)


def main(out: Path, n=60000):
    out.mkdir(parents=True, exist_ok=True)
    left, right = [], []
    for i in range(n):
        left.append(("u%07d" % i, "k", "L"))
        # 前 80% 的键在右侧存在（1:1），后 20% 左独有
        if i < int(n * 0.8):
            right.append(("u%07d" % i, "k", "R"))
    # 少量 1:2 重复，保证非完全唯一
    for j in range(500):
        right.append(("u%07d" % j, "k", "R2"))
    write(out / "left.csv", left)
    write(out / "right.csv", right)
    inner_expect = int(n * 0.8) + 500
    print("n=%d expect inner=%d left=%d"
          % (n, inner_expect, inner_expect + (n - int(n * 0.8))))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures/uniform"),
         int(sys.argv[2]) if len(sys.argv) > 2 else 60000)
