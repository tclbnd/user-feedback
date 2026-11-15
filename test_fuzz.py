#!/usr/bin/env python3
"""差分模糊测试 + 变异测试：证明 stable_check 真的能抓到不稳定散射。"""

from __future__ import annotations

import random

import radix


def differential(rounds: int = 400) -> int:
    rand = random.Random(2026)
    bad = 0
    diagests = {}
    for trial in range(rounds):
        n = rand.choice([0, 1, 2, 3, 5, 17, 64, 65, 129, 1000])
        mode = rand.choice(["uniform", "small", "equal", "high"])
        if mode == "uniform":
            pairs = [[rand.getrandbits(32), i] for i in range(n)]
        elif mode == "small":
            pairs = [[rand.randrange(4), i] for i in range(n)]
        elif mode == "equal":
            pairs = [[7, i] for i in range(n)]
        else:
            pairs = [[((rand.choice([0, 1, 0xFF, 0x100, 0xFFFFFF]) << 24)
                       | rand.getrandbits(24)) & 0xFFFFFFFF, i] for i in range(n)]

        expected = [[k, v] for k, v in sorted(pairs, key=lambda p: p[0])]
        digests = set()
        for threads in (1, 2, 8):
            for chunk in (1, 3, 64, 100000):
                res = radix.stable_radix_sort(pairs, threads=threads, chunk=chunk, seed=trial)
                digests.add(res["digest"])
                if res["sorted"] != expected:
                    bad += 1
                    print("FAIL sorted", mode, n, threads, chunk)
                if res["stable_check"] is not True:
                    bad += 1
                    print("FAIL stable_check", mode, n, threads, chunk)
        if len(digests) != 1:
            bad += 1
            print("FAIL digest drift", mode, n)
        diagests[trial] = digests.pop()
    return bad


def mutation_block_reversal() -> bool:
    """变异 1：块内散射逆序 —— 输出可能仍是正确排列，但稳定位必须为假。"""
    original = radix._scatter_block

    def unstable(keys, values, start, end, base, shift, out_keys, out_values, out_src):
        pending = []
        for i in range(start, end):
            pending.append(((keys[i] >> shift) & 255, i))
        for bucket, i in reversed(pending):
            pos = base[bucket]
            out_keys[pos] = keys[i]
            out_values[pos] = values[i]
            out_src[pos] = i
            base[bucket] = pos + 1

    pairs = [[7, i] for i in range(50)]
    radix._scatter_block = unstable
    try:
        mutant = radix.stable_radix_sort(pairs, threads=1, chunk=64)
    finally:
        radix._scatter_block = original

    restored = radix.stable_radix_sort(pairs, threads=1, chunk=64)

    print("mutant1 sorted==ref:", mutant["sorted"] == [[7, i] for i in range(50)],
          "| mutant1 stable_check:", mutant["stable_check"],
          "| restored stable_check:", restored["stable_check"])
    return mutant["stable_check"] is False and restored["stable_check"] is True


def mutation_prefix_sum_order() -> bool:
    """变异 2：前缀和按块完成顺序而非块号汇总 —— 这正是要避免的次序漂移。"""
    original = radix._block_base_offsets

    def coupled(hists, starts):
        bases = [None] * len(hists)
        running = list(starts)
        for block_id in reversed(range(len(hists))):
            bases[block_id] = radix.array("I", running)
            for bucket in range(256):
                running[bucket] += hists[block_id][bucket]
        return bases

    pairs = [[i % 3, i] for i in range(200)]
    expected = [[k, v] for k, v in sorted(pairs, key=lambda p: p[0])]

    radix._block_base_offsets = coupled
    try:
        mutant = radix.stable_radix_sort(pairs, threads=1, chunk=16, seed=5)
    finally:
        radix._block_base_offsets = original

    restored = radix.stable_radix_sort(pairs, threads=1, chunk=16, seed=5)
    print("mutant2 sorted==ref:", mutant["sorted"] == expected,
          "| mutant2 stable_check:", mutant["stable_check"],
          "| restored ok:", restored["sorted"] == expected and restored["stable_check"])
    return (mutant["sorted"] != expected and mutant["stable_check"] is False
            and restored["sorted"] == expected and restored["stable_check"] is True)


def mutation_check() -> bool:
    return mutation_block_reversal() and mutation_prefix_sum_order()


if __name__ == "__main__":
    failures = differential()
    print("differential failures:", failures)
    ok = mutation_check()
    print("mutation detected:", ok)
    raise SystemExit(0 if failures == 0 and ok else 1)
