"""生成小型、可完整核对的连接夹具（确定性，无随机种子依赖）。

覆盖点：
  * 复合键两列同时参与匹配；
  * 热点键 H 两侧都大量重复（多对多笛卡尔展开）；
  * 普通键的 1:1、1:N、N:1、两侧完全重复行；
  * 空键分量（SQL NULL）：仅第一列空、仅第二列空、两列都空；
  * 左独有键、右独有键；空输入；
  * 值里带逗号、引号、换行，验证 CSV 引用转义与逐字匹配。
"""
import csv
import os
import sys
from pathlib import Path


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["key_a", "key_b", "value"])
        writer.writerows(rows)


def build(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    left = []
    right = []

    # 热点复合键 ("hot","g")：左 6 行、右 5 行 -> 30 条匹配（含完全重复行）。
    for i in range(6):
        left.append(("hot", "g", "L-hot-%d" % i))
    for j in range(5):
        right.append(("hot", "g", "R-hot-%d" % j))
    left.append(("hot", "g", "L-hot-0"))  # 与第 0 行完全重复
    right.append(("hot", "g", "R-hot-0"))

    # 第二个热点：键 ("hot","k")，左 4 右 4 -> 16 条，多热点同分区竞争。
    for i in range(4):
        left.append(("hot", "k", "L-k-%d" % i))
    for j in range(4):
        right.append(("hot", "k", "R-k-%d" % j))

    # 普通键：1:1、1:N、N:1。
    left.append(("a", "1", "one-to-one L"))
    right.append(("a", "1", "one-to-one R"))
    for i in range(3):
        left.append(("b", "2", "1N-L-%d" % i))
    right.append(("b", "2", "1N-R"))
    left.append(("c", "3", "N1-L"))
    for j in range(3):
        right.append(("c", "3", "N1-R-%d" % j))

    # 左独有（内连接消失，左外保留为未匹配）。
    left.append(("left", "only", "left-only-1"))
    left.append(("left", "only", "left-only-2"))
    # 右独有（两种连接都不直接出现）。
    right.append(("right", "only", "right-only"))

    # NULL 键的各种组合，两侧都放，验证互不匹配。
    left.append(("", "g", "L null first"))
    left.append(("hot", "", "L null second"))
    left.append(("", "", "L both null"))
    right.append(("", "g", "R null first"))
    right.append(("hot", "", "R null second"))
    right.append(("", "", "R both null"))

    # 特殊字符值（键保持普通字符，值用于检查 CSV 转义无损）。
    left.append(("q", "9", 'has,comma and "quote"'))
    right.append(("q", "9", "line1\nline2"))
    left.append(("q", "9", "unicode 中文"))
    right.append(("q", "9", "unicode 中文"))

    write_csv(directory / "left.csv", left)
    write_csv(directory / "right.csv", right)

    # 空输入夹具（仅表头），两侧分别为空。
    empty = directory / "empty"
    empty.mkdir(exist_ok=True)
    write_csv(empty / "left.csv", [])
    write_csv(empty / "right.csv", [])

    # 闭式预期计数（多重集行数），供不依赖 SQLite 的快速核对。
    expected = dict(
        # hot,g 7x6=42；hot,k 4x4=16；a,1=1；b,2=3；c,3=3；q,9=2x2=4
        inner=dict(rows=(7 * 6) + 16 + 1 + 3 + 3 + 4),
        left=dict(
            # 全部内连接匹配 69 + 左独有 2 + 左侧 NULL 键 3 个未匹配
            rows=(7 * 6) + 16 + 1 + 3 + 3 + 4 + 2 + 3,
            unmatched=2 + 3,
        ),
        null_left=3,
        null_right=3,
        hot_key=("hot", "g"),
        hot_multiplicity=(7, 6),
    )
    (directory / "expected.json").write_text(
        json_dumps(expected), encoding="utf-8")
    return expected


def json_dumps(obj):  # 局部封装，避免顶部 import 顺序噪音
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures/small")
    exp = build(target)
    print("fixture written to", target)
    print(exp)
