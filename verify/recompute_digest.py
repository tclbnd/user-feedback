"""独立复算 rows.csv 的行数与多重集校验和，核对 _SUCCESS 发布值。

代表第三方只拿到结果目录时的核对路径：不依赖引擎内部状态，仅按公开的
规范化约定（4 列字符串的紧凑 JSON 的 BLAKE2b-256，按 256 位整数取模累加）
重算，与 _SUCCESS 中的 multiset_rows / multiset_digest 比对。
"""
import csv
import hashlib
import json
import sys
from pathlib import Path


def recompute(rows_path):
    modulus = 1 << 256
    acc = 0
    count = 0
    with rows_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        for cells in reader:
            blob = json.dumps(cells, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            digest = hashlib.blake2b(blob, digest_size=32).digest()
            acc = (acc + int.from_bytes(digest, "big")) % modulus
            count += 1
    return count, "%064x" % acc


def main():
    result_dir = Path(sys.argv[1])
    success = json.loads((result_dir / "_SUCCESS").read_text("utf-8"))
    count, digest = recompute(result_dir / "rows.csv")
    ok = (count == success["multiset_rows"]
          and digest == success["multiset_digest"]
          and count == success["rows"])
    print("[%s] %s recompute rows=%d digest=%s"
          % ("PASS" if ok else "FAIL", result_dir.name, count, digest[:16]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
