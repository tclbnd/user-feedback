#!/usr/bin/env python3
"""radix.py 的验收测试。

python3 test_radix.py            # 常规规模
python3 -m pytest test_radix.py  # 同上（若安装了 pytest）
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import tempfile
import os

import radix

THREAD_SET = (1, 4, 8)
CHUNK_SET = (64, 4096)
UINT32_MAX = (1 << 32) - 1


def reference_stable_sort(pairs):
    """参考实现：Python 内置 sorted 是稳定排序，仅按 key 比较。"""
    return sorted(pairs, key=lambda p: p[0])


def reference_digest(pairs):
    """与基线 radix.py 相同口径：sha1(str([(k, v), ...]))。"""
    ordered = [(k, v) for k, v in reference_stable_sort(pairs)]
    return hashlib.sha1(str(ordered).encode()).hexdigest()


def make_case(name, n, rand):
    """构造 4 组分布：均匀 / 聚集 / 全等 / 稀疏高段。"""
    if name == "uniform":
        return [[rand.getrandbits(32), rand.randrange(1 << 20)] for _ in range(n)]
    if name == "clustered":
        centers = [rand.getrandbits(32) for _ in range(4)]
        out = []
        for i in range(n):
            c = centers[i % len(centers)]
            out.append([max(0, min(UINT32_MAX, c + rand.randint(-64, 64))), rand.randrange(1 << 20)])
        return out
    if name == "all_equal":
        return [[0x5A5A5A5A, i] for i in range(n)]
    if name == "sparse_high":
        # 高阶字节高度稀疏：只有少数几个高 8 位取值，低字节随机
        highs = [0xFF, 0xFE, 0x80, 0x7F, 0x01]
        return [[(highs[i % len(highs)] << 24) | rand.getrandbits(24), rand.randrange(1 << 20)]
                for i in range(n)]
    raise AssertionError(name)


def check_case(name, pairs):
    expected = [[k, v] for k, v in reference_stable_sort(pairs)]

    baseline = None
    for threads in THREAD_SET:
        for chunk in CHUNK_SET:
            for seed in (0, 1, 12345):
                res = radix.stable_radix_sort(pairs, threads=threads, chunk=chunk, seed=seed)
                ctx = "%s threads=%d chunk=%d seed=%d" % (name, threads, chunk, seed)

                # 1) 与参考稳定排序逐位一致（键 + 伴随值，含等键相对次序）
                assert res["sorted"] == expected, ctx + ": 与参考稳定排序不一致"

                # 2) 稳定性校验位
                assert res["stable_check"] is True, ctx + ": stable_check 为假"

                # 3) 4 遍桶分布齐全，每遍 256 桶且总和为 n
                assert len(res["pass_stats"]) == 4, ctx + ": pass_stats 遍数错误"
                for idx, st in enumerate(res["pass_stats"]):
                    counts = st["bucket_counts"]
                    assert len(counts) == 256, ctx + ": 第 %d 遍桶数不是 256" % idx
                    assert sum(counts) == len(pairs), ctx + ": 第 %d 遍计数总和不等于 n" % idx

                # 4) 摘要与参考一致，
                #    且不随线程数 / 分块 / 乱序分块种子变化
                ref_digest = reference_digest(pairs)
                assert res["digest"] == ref_digest, ctx + ": digest 与参考不符"
                if baseline is None:
                    baseline = (res["digest"], res["pass_stats"])
                else:
                    assert res["digest"] == baseline[0], ctx + ": digest 漂移"
                    assert res["pass_stats"] == baseline[1], ctx + ": 逐遍桶分布漂移"


def test_distributions():
    rand = random.Random(20261116)
    for name in ("uniform", "clustered", "all_equal", "sparse_high"):
        check_case(name, make_case(name, 5000, rand))


def test_edges():
    rand = random.Random(7)
    cases = [
        [],
        [[0, "a"]],
        [[UINT32_MAX, "hi"], [0, "lo"]],
        [[3, None], [3, None], [3, {"x": 1}], [3, [1, 2]]],
    ]
    for pairs in cases:
        check_case("edge", pairs)
    assert radix.stable_radix_sort([], threads=8, chunk=1)["digest"] == \
        hashlib.sha1(b"[]").hexdigest()
    # chunk 大于输入长度
    check_case("edge", make_case("uniform", 10, rand))


def test_cli_roundtrip():
    rand = random.Random(99)
    pairs = make_case("uniform", 3000, rand)
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "in.json")
        outs = []
        for threads, chunk, seed in ((1, 64, 1), (8, 512, 42), (4, 1000, 7)):
            out = os.path.join(tmp, "out_%d_%d_%d.json" % (threads, chunk, seed))
            with open(inp, "w", encoding="utf-8") as f:
                json.dump({"pairs": pairs, "threads": threads, "chunk": chunk, "seed": seed}, f)
            assert radix.main(["radix.py", inp, out]) == 0
            outs.append(out)
        blobs = [open(o, "rb").read() for o in outs]
        assert blobs[0] == blobs[1] == blobs[2], "CLI 输出不是逐字节一致"


def test_large_million_smoke():
    """百万级输入：只校验摘要一致与稳定性位，控制运行时间。"""
    rand = random.Random(424242)
    n = 1_000_000
    keys = [rand.getrandbits(32) for _ in range(n)]
    pairs = [[k, i] for i, k in enumerate(keys)]
    a = radix.stable_radix_sort(pairs, threads=1, chunk=8192, seed=0)
    b = radix.stable_radix_sort(pairs, threads=8, chunk=65536, seed=3)
    assert a["stable_check"] and b["stable_check"]
    assert a["digest"] == b["digest"]
    assert a["pass_stats"] == b["pass_stats"]
    ref = reference_digest(pairs)
    assert a["digest"] == ref


if __name__ == "__main__":
    test_distributions()
    print("distributions ok")
    test_edges()
    print("edges ok")
    test_cli_roundtrip()
    print("cli ok")
    if "--large" in sys.argv:
        test_large_million_smoke()
        print("million ok")
    print("ALL TESTS PASSED")