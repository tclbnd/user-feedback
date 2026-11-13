"""用 SQLite 对连接结果做逐元组、逐重复次数的交叉核对。

参考结果完全由 SQLite 计算：空键按 SQL NULL 导入，用标准 JOIN ... ON
键相等（SQL 中 NULL 不相等，自然复现 NULL 永不匹配），左外用 LEFT JOIN。
随后把两侧结果都归约成 (元组 -> 出现次数) 的字典做精确多重集比较，
并独立复算 rows.csv 的多重集校验和，与 _SUCCESS 中发布的值核对。
"""
import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

HEADER = ["key_a", "key_b", "left_value", "right_value"]


def load_table(conn, path, alias):
    conn.execute(
        "CREATE TABLE %s (key_a TEXT, key_b TEXT, value TEXT)" % alias)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        assert header == ["key_a", "key_b", "value"]
        rows = []
        for record in reader:
            if not record or all(c == "" for c in record):
                continue
            ka = None if record[0] == "" else record[0]
            kb = None if record[1] == "" else record[1]
            rows.append((ka, kb, record[2]))
    conn.executemany(
        "INSERT INTO %s VALUES (?,?,?)" % alias, rows)
    conn.commit()


def reference_multiset(left_path, right_path, join_type):
    conn = sqlite3.connect(":memory:")
    load_table(conn, left_path, "l")
    load_table(conn, right_path, "r")
    # 必须用普通等值 "=" 而非空值安全的 "IS"：SQL 中 NULL = NULL 结果为
    # NULL（不为真），ON 条件不成立，因此含空键分量的行永不匹配。
    on = "l.key_a = r.key_a AND l.key_b = r.key_b"
    if join_type == "inner":
        sql = ("SELECT l.key_a, l.key_b, l.value, r.value FROM l JOIN r ON %s"
               % on)
    else:
        sql = ("SELECT l.key_a, l.key_b, l.value, r.value FROM l "
               "LEFT JOIN r ON %s" % on)
    counts = {}
    for ka, kb, lv, rv in conn.execute(sql):
        cells = tuple("" if v is None else v for v in (ka, kb, lv, rv))
        counts[cells] = counts.get(cells, 0) + 1
    conn.close()
    return counts


def actual_multiset(rows_path):
    counts = {}
    acc = 0
    modulus = 1 << 256
    total = 0
    with rows_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        assert header == HEADER, header
        for cells in reader:
            cells = tuple(cells)
            counts[cells] = counts.get(cells, 0) + 1
            blob = json.dumps(list(cells), ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            digest = hashlib.blake2b(blob, digest_size=32).digest()
            acc = (acc + int.from_bytes(digest, "big")) % modulus
            total += 1
    return counts, total, "%064x" % acc


def run(left_path, right_path, result_dir, join_type):
    result_dir = Path(result_dir)
    expected = reference_multiset(Path(left_path), Path(right_path), join_type)
    actual, rows, digest = actual_multiset(result_dir / "rows.csv")
    problems = []
    if expected != actual:
        only_ref = {k: v for k, v in expected.items() if actual.get(k) != v}
        only_got = {k: v for k, v in actual.items() if expected.get(k) != v}
        problems.append("多重集不一致")
        problems.append("  仅参考侧(元组->次数): %r" % only_ref)
        problems.append("  仅实现侧(元组->次数): %r" % only_got)
    success = json.loads((result_dir / "_SUCCESS").read_text("utf-8"))
    if success["rows"] != rows:
        problems.append("_SUCCESS.rows=%d 与实际 %d 不符"
                        % (success["rows"], rows))
    if success["multiset_digest"] != digest:
        problems.append("校验和不一致: 发布=%s 复算=%s"
                        % (success["multiset_digest"], digest))
    if success["multiset_rows"] != rows:
        problems.append("multiset_rows 与实际行数不符")
    return rows, digest, problems, success


def main():
    if len(sys.argv) != 5:
        print("用法: python3 sqlite_check.py left right result_dir inner|left",
              file=sys.stderr)
        return 2
    rows, digest, problems, success = run(*sys.argv[1:])
    status = "PASS" if not problems else "FAIL"
    print("[%s] %s join: rows=%d 多重集与 SQLite 完全一致"
          % (status, sys.argv[4], rows))
    if problems:
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print("  digest=%s" % digest)
    print("  spill=%d B  managed_peak=%d B  rss_peak=%d B  fds_peak=%d/%d"
          % (success["spill_bytes"], success["peak_managed_bytes"],
             success["peak_rss_bytes"], success["peak_open_fds"],
             success["max_open_fds"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
