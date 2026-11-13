"""生成“热点分区体积随比例 s=1..8 翻倍”的大型夹具与闭式计数清单。

热点复合键 ("hot","g")：左侧 300000*s 行、右侧固定 3 行。每行约 36 字节，
s=8 时热点分区约 86 MB（>64 MiB 预算），无法整装入内存，必须走热点剥离 +
分段块连接；输出 300000*s*3 条，规模可闭式核对，且不会产生无法落地的
天文级笛卡尔积。第二个热点键 ("hot","k") 5x7=35 条用于覆盖多热点。
另含冷键、左独有键与 NULL 键，覆盖左外未匹配与空键语义。
"""
import csv
import json
import sys
from pathlib import Path

SCALES = (1, 2, 4, 8)
BASE = 500_000
R_HOT_G = 3
PAYLOAD_LEN = 24


def _payload(prefix, idx):
    # 固定宽度负载，便于按行数闭式估算热点分区字节数。
    return ("%s-%08d" % (prefix, idx)).ljust(PAYLOAD_LEN, "x")


def build(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    manifest = dict(scales=list(SCALES), base=BASE, r_hot_g=R_HOT_G,
                    payload_len=PAYLOAD_LEN, cases={})
    for s in SCALES:
        case_dir = directory / ("s%d" % s)
        case_dir.mkdir(exist_ok=True)
        lp = case_dir / "left.csv"
        rp = case_dir / "right.csv"
        nl = nr = 0
        with lp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["key_a", "key_b", "value"])
            for i in range(BASE * s):
                w.writerow(("hot", "g", _payload("LG", i)))
                nl += 1
            for i in range(5):  # 第二热点键左 5 行
                w.writerow(("hot", "k", _payload("LK", i)))
                nl += 1
            for i in range(20):  # 冷键：各 1:1
                w.writerow(("cold%02d" % i, "c", _payload("LC", i)))
                nl += 1
            for i in range(10):  # 左独有键
                w.writerow(("only%02d" % i, "o", _payload("LO", i)))
                nl += 1
            w.writerow(("", "g", "null first"))      # NULL 键 x3
            w.writerow(("hot", "", "null second"))
            w.writerow(("", "", "both null"))
            nl += 3
        with rp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["key_a", "key_b", "value"])
            for j in range(R_HOT_G):  # 热点键右 3 行
                w.writerow(("hot", "g", _payload("RG", j)))
                nr += 1
            for j in range(7):  # 第二热点键右 7 行 -> 35 条
                w.writerow(("hot", "k", _payload("RK", j)))
                nr += 1
            for i in range(20):  # 冷键 1:1
                w.writerow(("cold%02d" % i, "c", _payload("RC", i)))
                nr += 1
            w.writerow(("", "g", "r null first"))    # 右侧 NULL，不匹配
            nr += 1

        # 闭式多重集计数：按“键类别 -> 输出条数”给出，可逐桶核对。
        hot_g = BASE * s * R_HOT_G
        hot_k = 5 * 7
        cold = 20
        inner_rows = hot_g + hot_k + cold
        left_unmatched = 10 + 3   # 左独有 10 + 左侧 NULL 3
        left_rows = inner_rows + left_unmatched
        manifest["cases"]["s%d" % s] = dict(
            left_input_rows=nl, right_input_rows=nr,
            buckets=dict(
                hot_g=hot_g, hot_k=hot_k, cold_one_to_one=cold,
                left_only=10, null_left_unmatched=3, right_null_ignored=1),
            inner_rows=inner_rows,
            left_rows=left_rows,
            left_unmatched=left_unmatched,
            hot_g_left_rows=BASE * s, hot_g_right_rows=R_HOT_G,
        )
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    return manifest


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures/large")
    m = build(target)
    total = sum(c["left_input_rows"] + c["right_input_rows"]
                for c in m["cases"].values())
    print("large fixture at", target, "total input rows:", total)
    for s in SCALES:
        c = m["cases"]["s%d" % s]
        print("s=%d inner=%d left=%d" % (s, c["inner_rows"], c["left_rows"]))
