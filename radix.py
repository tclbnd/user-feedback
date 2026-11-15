#!/usr/bin/env python3
"""稳定 8 位桶 LSD 基数排序（分块计数 / 全局前缀和 / 稳定散射）。

用法:
    python3 radix.py input.json output.json

input.json:
    {"pairs": [[key32, value], ...], "threads": 4, "chunk": 64, "seed": 1}

输出:
    {"sorted": [[k, v], ...],
     "pass_stats": [{"bucket_counts": [256 个整数]}, ... 共 4 遍 ...],
     "stable_check": bool,
     "digest": "sha1"}

核心不变量
----------
1. 4 遍 LSD，每遍只处理一个 8 位字节，桶号升序输出。
2. 每遍的散射都按“块号升序、块内源次序”进行；线程只影响块计数的
   完成顺序，不影响块号拼接顺序，因此任何 threads/chunk/seed 组合下
   最终排列完全一致。
3. 每遍结束后校验每个桶内元素在“本遍输入序列”中的下标严格递增，
   即该遍稳定；4 遍全部稳定 + 最终键非降，stable_check 才为 True。
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from array import array
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Sequence

RADIX_BITS = 8
RADIX = 1 << RADIX_BITS          # 256 个桶
DIGIT_MASK = RADIX - 1
KEY_BITS = 32
PASSES = KEY_BITS // RADIX_BITS  # 4 遍
UINT32_MAX = (1 << 32) - 1

# 每个块保存 256 个直方图计数与 256 个块内起始偏移。限制块数可避免
# 极端小的 chunk 在百万级输入上产生过多元数据；这是内部资源上限，
# 不改变排序结果。
MAX_CHUNKS = 16384

# 索引用无符号定长数组：优先 4 字节，"I" 不足 4 字节的平台退化为 "Q"。
INDEX_TYPECODE = "I" if array("I").itemsize >= 4 else "Q"


def _index_array(values: Iterable[int] = ()) -> array:
    return array(INDEX_TYPECODE, values)


def _zero_array(n: int) -> array:
    """构造 n 个 0 的无符号数组（比 array(tc, [0]) * n 更快且内存更省）。"""
    return array(INDEX_TYPECODE, bytes(n * array(INDEX_TYPECODE).itemsize))


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _effective_chunk_size(n: int, chunk: int) -> int:
    """把用户 chunk 作为下限使用；输入特别大时适当合并块以限制内存。"""
    if n <= 0:
        return max(1, chunk)
    min_chunk = (n + MAX_CHUNKS - 1) // MAX_CHUNKS
    return max(chunk, min_chunk)


def _make_ranges(n: int, chunk: int) -> list[tuple[int, int]]:
    return [(start, min(start + chunk, n)) for start in range(0, n, chunk)]


def _shuffled_block_order(block_count: int, seed: int) -> list[int]:
    """返回乱序的块调度序列；只影响任务提交顺序，不影响块号语义。"""
    order = list(range(block_count))
    if block_count > 1:
        random.Random(seed).shuffle(order)
    return order


# ---------------------------------------------------------------------------
# 阶段 1：块内计数（可并行，天然与块顺序无关）
# ---------------------------------------------------------------------------

def _count_block(keys: Sequence[int], start: int, end: int, shift: int) -> list[int]:
    hist = [0] * RADIX
    for i in range(start, end):
        hist[(keys[i] >> shift) & DIGIT_MASK] += 1
    return hist


def _count_blocks(
    keys: Sequence[int],
    ranges: Sequence[tuple[int, int]],
    shift: int,
    threads: int,
    order: Sequence[int],
) -> list[list[int]]:
    block_count = len(ranges)
    hists: list[list[int] | None] = [None] * block_count

    if threads <= 1 or block_count <= 1:
        for block_id in order:
            start, end = ranges[block_id]
            hists[block_id] = _count_block(keys, start, end, shift)
        return hists  # type: ignore[return-value]

    with ThreadPoolExecutor(
        max_workers=min(threads, block_count), thread_name_prefix="radix-count"
    ) as pool:
        # 按乱序提交；结果仍按 block_id 归档，完成顺序不参与任何决策。
        futures = [
            (
                block_id,
                pool.submit(
                    _count_block, keys, ranges[block_id][0], ranges[block_id][1], shift
                ),
            )
            for block_id in order
        ]
        for block_id, future in futures:
            hists[block_id] = future.result()
    return hists  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 阶段 2：全局直方图与前缀和（严格按块号 0..m-1 汇总）
# ---------------------------------------------------------------------------

def _global_counts_and_starts(
    hists: Sequence[Sequence[int]],
) -> tuple[list[int], list[int], int]:
    counts = [0] * RADIX
    for hist in hists:
        for bucket in range(RADIX):
            counts[bucket] += hist[bucket]

    starts = [0] * RADIX
    total = 0
    for bucket in range(RADIX):
        starts[bucket] = total
        total += counts[bucket]
    return counts, starts, total


def _block_base_offsets(
    hists: Sequence[Sequence[int]], starts: Sequence[int]
) -> list[array]:
    """为每个块预计算 256 个桶的写入起点。

    base[b] = 全局桶起点 + 前面所有块在该桶中的元素数。
    各块写区间互不相交，因此散射可以并行且结果与调度顺序无关。
    """
    bases: list[array] = []
    running = list(starts)
    for hist in hists:
        bases.append(_index_array(running))
        for bucket in range(RADIX):
            running[bucket] += hist[bucket]
    return bases


# ---------------------------------------------------------------------------
# 阶段 3：按块散射（稳定：块号升序、块内源序）
# ---------------------------------------------------------------------------

def _scatter_block(
    keys: Sequence[int],
    values: Sequence[Any],
    start: int,
    end: int,
    base: array,
    shift: int,
    out_keys: array,
    out_values: list[Any],
    out_src: array,
) -> None:
    for i in range(start, end):
        bucket = (keys[i] >> shift) & DIGIT_MASK
        pos = base[bucket]
        out_keys[pos] = keys[i]
        out_values[pos] = values[i]
        out_src[pos] = i          # 本遍输入序列中的位置，用于稳定性校验
        base[bucket] = pos + 1


def _scatter_blocks(
    keys: Sequence[int],
    values: Sequence[Any],
    ranges: Sequence[tuple[int, int]],
    bases: Sequence[array],
    shift: int,
    n: int,
    threads: int,
    order: Sequence[int],
) -> tuple[array, list[Any], array]:
    out_keys = _zero_array(n)
    out_values: list[Any] = [None] * n
    out_src = _zero_array(n)

    if threads <= 1 or len(ranges) <= 1:
        for block_id in order:
            start, end = ranges[block_id]
            _scatter_block(
                keys, values, start, end, bases[block_id], shift,
                out_keys, out_values, out_src,
            )
        return out_keys, out_values, out_src

    with ThreadPoolExecutor(
        max_workers=min(threads, len(ranges)), thread_name_prefix="radix-scatter"
    ) as pool:
        futures = [
            pool.submit(
                _scatter_block,
                keys, values,
                ranges[block_id][0], ranges[block_id][1],
                bases[block_id], shift,
                out_keys, out_values, out_src,
            )
            for block_id in order
        ]
        for future in futures:
            future.result()
    return out_keys, out_values, out_src


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _pass_is_stable(
    out_src: Sequence[int], counts: Sequence[int], starts: Sequence[int]
) -> bool:
    """每个桶内的源下标必须严格递增——即本遍没有打乱相等数字的相对次序。"""
    for bucket in range(RADIX):
        start = starts[bucket]
        end = start + counts[bucket]
        previous = -1
        for pos in range(start, end):
            src = out_src[pos]
            if src <= previous:
                return False
            previous = src
    return True


def _is_non_decreasing(keys: Sequence[int]) -> bool:
    previous = -1
    for key in keys:
        if key < previous:
            return False
        previous = key
    return True


def _digest_pairs(keys: Sequence[int], values: Sequence[Any]) -> str:
    """与 hashlib.sha1(str([(k, v), ...]).encode()) 完全一致的流式计算。"""
    digest = hashlib.sha1()
    digest.update(b"[")
    for i in range(len(keys)):
        if i:
            digest.update(b", ")
        digest.update(("(%r, %r)" % (int(keys[i]), values[i])).encode("utf-8"))
    digest.update(b"]")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def _validate_params(threads: Any, chunk: Any, seed: Any) -> tuple[int, int, int]:
    if not isinstance(threads, int) or isinstance(threads, bool) or threads < 1:
        raise ValueError("threads 必须是 >=1 的整数")
    if not isinstance(chunk, int) or isinstance(chunk, bool) or chunk < 1:
        raise ValueError("chunk 必须是 >=1 的整数")
    if seed is None:
        seed = 0
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed 必须是整数")
    return threads, chunk, seed


def _coerce_pairs(pairs: Iterable[Sequence[Any]]) -> tuple[array, list[Any], int]:
    pairs = list(pairs)
    n = len(pairs)
    keys = _zero_array(n)
    values: list[Any] = [None] * n
    for i, pair in enumerate(pairs):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("pairs[%d] 必须是 [key, value]" % i)
        key, value = pair
        if not isinstance(key, int) or isinstance(key, bool):
            raise ValueError("pairs[%d][0] 必须是整数键" % i)
        if key < 0 or key > UINT32_MAX:
            raise ValueError("pairs[%d][0] 超出 32 位无符号键范围: %d" % (i, key))
        keys[i] = key
        values[i] = value
    return keys, values, n


def stable_radix_sort(
    pairs: Iterable[Sequence[Any]],
    threads: int = 4,
    chunk: int = 64,
    seed: int = 0,
) -> dict[str, Any]:
    """对 (key32, value) 序列做稳定 LSD 基数排序并返回结果字典。"""
    threads, chunk, seed = _validate_params(threads, chunk, seed)
    keys, values, n = _coerce_pairs(pairs)

    effective_chunk = _effective_chunk_size(n, chunk)
    ranges = _make_ranges(n, effective_chunk)
    block_count = len(ranges)

    pass_stats: list[dict[str, list[int]]] = []
    all_stable = True
    current_keys = keys
    current_values = values

    for pass_index in range(PASSES):
        shift = pass_index * RADIX_BITS

        # 阶段 1：块内计数（可乱序、可并行）
        count_order = _shuffled_block_order(
            block_count, seed + pass_index * 0x9E3779B1
        )
        hists = _count_blocks(current_keys, ranges, shift, threads, count_order)

        # 阶段 2：全局计数 + 前缀和（严格按块号，和线程调度完全解耦）
        counts, starts, total = _global_counts_and_starts(hists)
        if total != n:
            raise RuntimeError("块直方图总数与元素数不一致")

        # 阶段 3：块级起始偏移 + 稳定散射（各块写区间互不相交）
        bases = _block_base_offsets(hists, starts)
        scatter_order = _shuffled_block_order(
            block_count, seed + pass_index * 0x85EBCA6B + 0x165667B1
        )
        out_keys, out_values, out_src = _scatter_blocks(
            current_keys, current_values, ranges, bases, shift, n,
            threads, scatter_order,
        )

        # 阶段 4：本遍稳定性校验
        pass_stable = _pass_is_stable(out_src, counts, starts)
        all_stable = all_stable and pass_stable
        pass_stats.append({"bucket_counts": counts})

        current_keys, current_values = out_keys, out_values

    stable_check = bool(all_stable and _is_non_decreasing(current_keys))

    sorted_pairs = [[int(current_keys[i]), current_values[i]] for i in range(n)]
    digest = _digest_pairs(current_keys, current_values)

    return {
        "sorted": sorted_pairs,
        "pass_stats": pass_stats,
        "stable_check": stable_check,
        "digest": digest,
    }


def radix_sort(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """stable_radix_sort 的别名。"""
    return stable_radix_sort(*args, **kwargs)


def run_request(request: dict[str, Any]) -> dict[str, Any]:
    if "pairs" not in request:
        raise ValueError("输入缺少 pairs")
    return stable_radix_sort(
        request["pairs"],
        threads=request.get("threads", 4),
        chunk=request.get("chunk", 64),
        seed=request.get("seed", 0),
    )


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) != 3:
        print("用法: %s input.json output.json" % argv[0], file=sys.stderr)
        return 2

    try:
        with open(argv[1], "r", encoding="utf-8") as f:
            request = json.load(f)
        result = run_request(request)
        with open(argv[2], "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("radix: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
