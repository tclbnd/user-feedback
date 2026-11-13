"""验收 2 的流式核对器（不把大结果读回内存）。

逐条扫描 rows.csv，按闭式清单中的“键类别桶”累计输出条数，与 manifest 的
闭式计数逐项比对；同时复算多重集校验和与 _SUCCESS 核对，并断言：
  * 托管工作内存峰值不超过 64 MiB 预算；
  * 峰值打开句柄数不超过上限；
  * 随热点体积 1->8 翻倍，托管内存峰值基本持平（内存有界，不随数据增长）；
  * 溢写量随数据规模线性增长。
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

BUDGET_64MIB = 64 << 20


def stream_buckets(rows_path):
    buckets = dict(hot_g=0, hot_k=0, cold=0,
                   left_only=0, null_unmatched=0, other=0)
    acc = 0
    modulus = 1 << 256
    total = 0
    with rows_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        for ka, kb, lv, rv in reader:
            total += 1
            blob = json.dumps([ka, kb, lv, rv], ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            digest = hashlib.blake2b(blob, digest_size=32).digest()
            acc = (acc + int.from_bytes(digest, "big")) % modulus
            if ka == "hot" and kb == "g":
                buckets["hot_g"] += 1
            elif ka == "hot" and kb == "k":
                buckets["hot_k"] += 1
            elif ka.startswith("cold") and kb == "c":
                buckets["cold"] += 1
            elif ka.startswith("only") and kb == "o":
                buckets["left_only"] += 1
            elif ka == "" or kb == "":
                buckets["null_unmatched"] += 1
            else:
                buckets["other"] += 1
    return total, "%064x" % acc, buckets


def check_case(case_dir, expected, jtype, max_open):
    rows_path = case_dir / "rows.csv"
    total, digest, buckets = stream_buckets(rows_path)
    success = json.loads((case_dir / "_SUCCESS").read_text("utf-8"))
    problems = []
    want_rows = expected["inner_rows"] if jtype == "inner" \
        else expected["left_rows"]
    if total != want_rows:
        problems.append("总行数 %d != 闭式期望 %d" % (total, want_rows))
    if buckets["hot_g"] != expected["buckets"]["hot_g"]:
        problems.append("hot_g 桶 %d != %d"
                        % (buckets["hot_g"], expected["buckets"]["hot_g"]))
    if buckets["hot_k"] != expected["buckets"]["hot_k"]:
        problems.append("hot_k 桶 %d != %d"
                        % (buckets["hot_k"], expected["buckets"]["hot_k"]))
    if buckets["cold"] != expected["buckets"]["cold_one_to_one"]:
        problems.append("cold 桶 %d != %d"
                        % (buckets["cold"],
                           expected["buckets"]["cold_one_to_one"]))
    if buckets["other"] != 0:
        problems.append("出现未分类行 %d" % buckets["other"])
    if jtype == "inner":
        if buckets["left_only"] or buckets["null_unmatched"]:
            problems.append("内连接出现未匹配行: %r" % buckets)
    else:
        if buckets["left_only"] != 10:
            problems.append("左独有未匹配 %d != 10" % buckets["left_only"])
        if buckets["null_unmatched"] != 3:
            problems.append("NULL 未匹配 %d != 3" % buckets["null_unmatched"])
    if success["multiset_digest"] != digest:
        problems.append("校验和复算不一致")
    if success["multiset_rows"] != total:
        problems.append("_SUCCESS 行数与流式计数不一致")
    if success["peak_managed_bytes"] > BUDGET_64MIB:
        problems.append("托管峰值 %d 超过 64MiB 预算"
                        % success["peak_managed_bytes"])
    if success["peak_open_fds"] > max_open:
        problems.append("句柄峰值 %d 超过上限 %d"
                        % (success["peak_open_fds"], max_open))
    if success["spill_bytes"] <= 0:
        problems.append("未记录到溢写量")
    return problems, success, buckets


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "fixtures" / "large"
                           / "manifest.json").read_text("utf-8"))
    runs = root / "runs" / "large"
    max_open = 12
    failures = 0
    history = {}
    for s in manifest["scales"]:
        expected = manifest["cases"]["s%d" % s]
        case_dir = runs / ("s%d_inner" % s)
        problems, success, buckets = check_case(
            case_dir, expected, "inner", max_open)
        history[s] = success
        ok = not problems
        failures += 0 if ok else 1
        print(("[%s] s=%d inner rows=%-8d hot_g=%-8d managed_peak=%d "
               "rss=%d spill=%dMB fds=%d/%d hot_keys=%d segments=%d")
              % ("PASS" if ok else "FAIL", s, expected["inner_rows"],
                 buckets["hot_g"], success["peak_managed_bytes"],
                 success["peak_rss_bytes"],
                 success["spill_bytes"] >> 20, success["peak_open_fds"],
                 success["max_open_fds"], success["hot_keys"],
                 success["segment_passes"]))
        for p in problems:
            print("    " + p, file=sys.stderr)

    # 最大规模再跑一次左外，验证未匹配/NULL 路径与大输出。
    s = 8
    expected = manifest["cases"]["s8"]
    problems, success, buckets = check_case(
        runs / "s8_left", expected, "left", max_open)
    ok = not problems
    failures += 0 if ok else 1
    print(("[%s] s=8 left  rows=%-8d unmatched(only=%d null=%d) "
           "managed_peak=%d spill=%dMB")
          % ("PASS" if ok else "FAIL", expected["left_rows"],
             buckets["left_only"], buckets["null_unmatched"],
             success["peak_managed_bytes"], success["spill_bytes"] >> 20))
    for p in problems:
        print("    " + p, file=sys.stderr)

    # 有界性：数据翻 8 倍，托管内存峰值不得同步膨胀（允许 25% 抖动）。
    base_peak = history[1]["peak_managed_bytes"]
    big_peak = history[8]["peak_managed_bytes"]
    ratio = big_peak / max(1, base_peak)
    if ratio <= 1.25:
        print("[PASS] 内存有界: s1 托管峰值=%d, s8=%d, 比值=%.2f (<=1.25)"
              % (base_peak, big_peak, ratio))
    else:
        print("[FAIL] 托管内存随数据膨胀: 比值=%.2f" % ratio, file=sys.stderr)
        failures += 1
    # 溢写量应随规模近似线性（s8/s1 应接近 8）。
    spill_ratio = history[8]["spill_bytes"] / max(1, history[1]["spill_bytes"])
    if spill_ratio >= 6.0:
        print("[PASS] 溢写随数据线性增长: s8/s1=%.2f" % spill_ratio)
    else:
        print("[FAIL] 溢写增长异常: 比值=%.2f" % spill_ratio, file=sys.stderr)
        failures += 1

    print("\n大型夹具验收结论:", "全部通过" if failures == 0
          else ("%d 项失败" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
