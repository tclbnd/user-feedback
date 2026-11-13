# -*- coding: utf-8 -*-
"""导出固定种子的 16 分区基准数据（可直接作为 apply 输入）。

用法：
    python3 tools/make_bench.py uniform > /tmp/uniform.json
    python3 tools/make_bench.py longtail --algorithm q1 > /tmp/longtail.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qsummary.bench import build_partitions  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("uniform", "longtail", "duplicates"))
    parser.add_argument("--algorithm", default="q1")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    kwargs = {} if args.seed is None else {"seed": args.seed}
    partitions = build_partitions(args.kind, **kwargs)
    payload = {"partitions": [
        {
            "index": i,
            "revision": 1,
            "values": partitions[i],
            "algorithm": args.algorithm,
        }
        for i in sorted(partitions)
    ]}
    json.dump(payload, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
