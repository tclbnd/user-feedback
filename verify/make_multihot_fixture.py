"""生成两侧都很大、含多个热点键的多对多夹具，迫使分段块连接双侧切块。

两个热点键各自在两侧都有大量重复；再叠加普通键、左独有与 NULL 键。
在小内存预算下，单个热点键的任一侧都无法整块装入内存，分段连接必须对
两侧都进行切块，仍产出正确的笛卡尔积。
"""
import csv
import sys
from pathlib import Path


def write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["key_a", "key_b", "value"])
        w.writerows(rows)


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    left, right = [], []
    # 热点 H1：左 30000 右 100 -> 300 万；热点 H2：左 20000 右 150 -> 300 万。
    # 左侧单热点 3 万行（约 0.9MB 磁盘、记账放大后远超 1MiB 预算），迫使分段
    # 块连接对较大的左侧切块、右侧流式重复扫描，覆盖“两侧均为多对多重复”。
    for i in range(30000):
        left.append(("H1", "x", "l1-%06d" % i))
    for j in range(100):
        right.append(("H1", "x", "r1-%06d" % j))
    for i in range(20000):
        left.append(("H2", "y", "l2-%06d" % i))
    for j in range(150):
        right.append(("H2", "y", "r2-%06d" % j))
    # 普通 1:1、左独有、NULL。
    for i in range(50):
        left.append(("cold", str(i), "lc"))
        right.append(("cold", str(i), "rc"))
    left.append(("only", "z", "lo"))
    left.append(("", "x", "null"))
    right.append(("H1", "", "rnull"))
    write(out / "left.csv", left)
    write(out / "right.csv", right)
    print("rows left=%d right=%d; expect inner=%d left=%d"
          % (len(left), len(right),
             30000 * 100 + 20000 * 150 + 50,
             30000 * 100 + 20000 * 150 + 50 + 2))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures/multihot"))
