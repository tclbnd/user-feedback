#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实系统时钟下的验收: 16 线程 10^6 + 重启续号。

与 selftest.py 的区别: 这里用 SystemClock(time.time_ns) 而不是虚拟时钟,
因此会真实经过"同一毫秒序号耗尽 -> 真实 sleep 到下一毫秒"路径。
"""

import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from idgen import ClockRollbackIdGenerator, SystemClock, MAX_SEQ, SEQ_BITS

TOTAL = 1_000_000
THREADS = 16


def ts_of(i):
    return i >> SEQ_BITS


def main():
    tmp = tempfile.mkdtemp(prefix="idgen-real-")
    path = os.path.join(tmp, "state.json")
    gen = ClockRollbackIdGenerator(clock=SystemClock(), state_path=path,
                                   clock_id="real-node", max_wait_ms=5)
    per = TOTAL // THREADS
    results = [None] * THREADS
    barrier = threading.Barrier(THREADS)

    def worker(idx):
        barrier.wait()
        local = []
        remaining = per
        while remaining:
            n = 128 if remaining >= 128 else remaining
            local.extend(gen.take(n))
            remaining -= n
        results[idx] = local

    started = time.time()
    workers = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    elapsed = time.time() - started

    flat = [i for r in results for i in r]
    total = len(flat)
    uniq = len(set(flat))
    ordered = all(all(b > a for a, b in zip(r, r[1:])) for r in results)
    last_id = max(flat)
    last_ts = ts_of(last_id)
    stats = dict(gen.stats)
    gen.close()

    print("生成数      :", total)
    print("唯一数      :", uniq, "-> 无重复" if uniq == total else "-> 有重复!")
    print("线程内单调  :", ordered)
    print("耗时        : %.2fs (%.0f id/s)" % (elapsed, total / elapsed))
    print("seq耗尽等待 :", stats["wait_seq_exhausted"])
    print("强制推进    :", stats["forced_advance"])
    print("来源分布    : normal=%d wait=%d drift=%d"
          % (stats["normal"], stats["wait"], stats["drift"]))
    print("关闭前最后时间位:", last_ts)

    # ---- 重启: 真实时间可能比关闭前晚, 但也不允许回退时间位 ----
    gen2 = ClockRollbackIdGenerator(clock=SystemClock(), state_path=path,
                                    clock_id="real-node", max_wait_ms=5)
    first = gen2.take(1)[0]
    print("重启首 ID 时间位 :", ts_of(first), "(关闭前 %d)" % last_ts)
    gen2.close()
    ok = (total == TOTAL and uniq == total and ordered
          and ts_of(first) >= last_ts)
    print("\n结论:", "PASS" if ok else "FAIL")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())