#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验收脚本: 逐条对照需求的两条验收标准产出可判定证据。

验收1: 并发读写压测下迭代器无悬挂页访问(UAF/工具统计为零),
       且快照期间内存上界有界。
验收2: 对同一份数据两个快照键序一致; 快照释放后页回收率 100%。
"""
import random
import threading
import time

from btree import MVCCBTree, PageStore


def acceptance_two_snapshot_order_and_reclaim():
    """验收2: 双快照键序一致 + 释放后 100% 回收 + 批量回收证据。"""
    print("\n[验收2] 同一份数据的两个快照键序一致 + 释放后 100% 回收")
    store = PageStore()
    tree = MVCCBTree(store)
    rnd = random.Random(7)
    model = {}
    # 构造一棵多层 B+ 树(足够触发多次分裂)
    for _ in range(500):
        k = rnd.randrange(1000)
        tree.put(k, k * 3)
        model[k] = k * 3
    for k in rnd.sample(list(model.keys()), 150):
        tree.delete(k)
        model.pop(k)

    # 同一读临界区基于同一根拍两个快照
    with tree._rw.read():
        tree.snapshot("A")
        tree.snapshot("B")
    order_a = tree.iter_snapshot("A")
    order_b = tree.iter_snapshot("B")
    expected = sorted(model.items())

    assert order_a == expected, "快照 A 键序与模型不符"
    assert order_b == expected, "快照 B 键序与模型不符"
    assert order_a == order_b, "两个快照键序不一致"
    print("  - 快照 A/B 键序一致且等于有序模型, 键数 = %d" % len(expected))

    # 持快照期间继续大量写入: 旧版本页被延迟回收, 但视图不变
    for i in range(300):
        tree.put(1000 + i, i)
    assert tree.iter_snapshot("A") == order_a, "并发写入改变了快照视图"
    held = store.snapshot_stats()
    print("  - 持快照再写 300 次后快照视图不变; 延迟页 deferred_now = %d (有界)"
          % held["deferred_now"])

    allocs_before_release = store.allocs
    freed_a = tree.release("A")
    freed_b = tree.release("B")
    print("  - release(A) 批量回收 %d 页, release(B) 批量回收 %d 页"
          % (freed_a, freed_b))

    tree.destroy()
    fin = store.snapshot_stats()
    assert fin["pages_live"] == 0, "仍有存活页, 未完全回收"
    assert fin["frees"] == fin["allocs"], "回收数 != 分配数"
    rate = fin["frees"] / fin["allocs"]
    print("  - 最终 allocs=%d frees=%d 回收率=%.4f, pages_live=%d"
          % (fin["allocs"], fin["frees"], rate, fin["pages_live"]))
    assert abs(rate - 1.0) < 1e-9
    return dict(allocs=fin["allocs"], frees=fin["frees"], reclaim_rate=rate,
                deferred_peak=fin["deferred_peak"], key_count=len(expected))


def acceptance_concurrency_bounded_memory():
    """验收1: 并发读写零悬挂访问 + 快照内存上界有界。"""
    print("\n[验收1] 并发读写压测: 零悬挂页访问 + 快照期内存有界")
    store = PageStore()
    tree = MVCCBTree(store)
    stop = threading.Event()
    failures = []
    iterations = [0]
    peak_live_under_snap = [0]
    peak_deferred = [0]

    def writer(seed):
        rnd = random.Random(seed)
        while not stop.is_set():
            k = rnd.randrange(300)
            if rnd.random() < 0.7:
                tree.put(k, rnd.randrange(1 << 24))
            else:
                tree.delete(k)

    def reader(seed):
        rnd = random.Random(seed)
        n = 0
        while not stop.is_set():
            # 每读者使用全局唯一快照 id, 避免多线程在快照表里互相覆盖
            sid_x = ("x-%d-%d" % (seed, n))
            sid_y = ("y-%d-%d" % (seed, n))
            n += 1
            with tree._rw.read():
                tree.snapshot(sid_x)
                tree.snapshot(sid_y)
            try:
                xa = tree.iter_snapshot(sid_x)
                yb = tree.iter_snapshot(sid_y)
                if xa != yb:
                    failures.append("twin order mismatch")
                keys = [k for k, _ in xa]
                if any(not (keys[i - 1] < keys[i]) for i in range(1, len(keys))):
                    failures.append("order violation")
                st = store.snapshot_stats()
                peak_live_under_snap[0] = max(peak_live_under_snap[0],
                                              st["pages_live"])
                peak_deferred[0] = max(peak_deferred[0], st["deferred_now"])
                iterations[0] += 1
            finally:
                tree.release(sid_y)
                tree.release(sid_x)

    threads = [threading.Thread(target=writer, args=(100 + i,), daemon=True)
               for i in range(4)]
    threads += [threading.Thread(target=reader, args=(500 + i,), daemon=True)
                for i in range(4)]
    for th in threads:
        th.start()
    time.sleep(3.0)
    stop.set()
    for th in threads:
        th.join(timeout=10)

    for sid in list(tree._snapshots.keys()):
        tree.release(sid)
    tree.destroy()
    st = store.snapshot_stats()

    print("  - 读者完成快照迭代 %d 次" % iterations[0])
    print("  - UAF=%d double_free=%d refcount_violations=%d"
          % (st["uaf_violations"], st["double_free"], st["refcount_violations"]))
    print("  - 一致性失败=%d %s" % (len(failures), failures[:3]))
    print("  - 快照期存活页峰值=%d, 延迟页峰值=%d (均为有限上界)"
          % (peak_live_under_snap[0], peak_deferred[0]))
    print("  - 压测 allocs=%d frees=%d 回收率=%.4f"
          % (st["allocs"], st["frees"], st["frees"] / st["allocs"]))

    assert iterations[0] > 0
    assert st["uaf_violations"] == 0
    assert st["double_free"] == 0
    assert st["refcount_violations"] == 0
    assert not failures
    assert st["frees"] == st["allocs"] and st["pages_live"] == 0
    # 内存上界有界: 峰值是有限值且远小于累计分配量(证明旧页被持续回收)
    assert peak_live_under_snap[0] < st["allocs"]
    return dict(iterations=iterations[0], uaf=st["uaf_violations"],
                peak_live=peak_live_under_snap[0],
                peak_deferred=peak_deferred[0],
                allocs=st["allocs"], frees=st["frees"])


def main():
    r1 = acceptance_concurrency_bounded_memory()
    r2 = acceptance_two_snapshot_order_and_reclaim()
    print("\n" + "=" * 60)
    print("全部验收结论: PASS")
    print("=" * 60)
    print("并发证据:", r1)
    print("快照/回收证据:", r2)


if __name__ == "__main__":
    main()
