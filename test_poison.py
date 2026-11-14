#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
毒化检疫(quarantine)对抗测试 —— Python 侧等价于 ASAN 的悬挂访问检测。

机制同构: ASAN 在 free 后把内存页标记不可访问, 解引用即报错;
本实现 free 后把 pid 移入 quarantine 并从活页表删除, 之后任何
access/unref 都被守卫捕获并累计 uaf_violations/double_free, 同时抛出
DanglingPageError。pid 全局永不复用, 杜绝“新页复用旧号”掩盖悬挂。
"""
from btree import MVCCBTree, PageStore, DanglingPageError
import random


def test_quarantine_catches_dangling_access():
    store = PageStore()
    t = MVCCBTree(store)
    for k in range(60):
        t.put(k, k)
    snap = t.snapshot("s")
    pinned = list(snap.pinned)
    # 制造大量 COW, 旧页在无其他快照时应被回收进 quarantine
    for k in range(60, 200):
        t.put(k, k)
    t.release("s")
    assert store.uaf_violations == 0
    # 现在尝试访问每一个已回收 pid, 必须全部被守卫捕获
    caught = 0
    dead = list(store._quarantine)
    assert dead, "应当存在已回收页"
    for pid in dead:
        try:
            store.access(pid)
        except DanglingPageError:
            caught += 1
    assert caught == len(dead)
    assert store.uaf_violations == len(dead)
    print("  - 对 %d 个已回收页的访问全部被毒化守卫捕获(UAF 计数=%d)"
          % (len(dead), store.uaf_violations))


def test_double_unref_detected():
    store = PageStore()
    t = MVCCBTree(store)
    for k in range(40):
        t.put(k, k)
    dead = None
    # 等某页被回收后, 对它再次 unref 必须判 double-free
    t.snapshot("s")
    for k in range(40, 120):
        t.put(k, k)
    snap = t._snapshots["s"]
    # 手动多放一次引用制造双 unref
    target = snap.pinned[0]
    t.release("s")
    if target in store._quarantine:
        try:
            store.unref(target)
        except DanglingPageError:
            pass
    assert store.double_free >= 1 or store.uaf_violations >= 1
    print("  - 对已回收页再次 unref 被判定 double-free/UAF")


def test_pid_never_reused():
    store = PageStore()
    t = MVCCBTree(store)
    rnd = random.Random(11)
    for _ in range(500):
        k = rnd.randrange(80)
        if rnd.random() < 0.75:
            t.put(k, rnd.randrange(999))
        else:
            t.delete(k)
    # 分配过的 pid 集合大小应等于 allocs(每个 pid 唯一, 不复用)
    ever = set(store._quarantine) | set(store._pages)
    assert len(ever) == store.allocs, (len(ever), store.allocs)
    assert max(ever) == store.allocs
    print("  - %d 个 pid 全局唯一、永不复用" % store.allocs)


def test_snapshot_prevents_reclaim_then_bulk_free():
    store = PageStore()
    t = MVCCBTree(store)
    for k in range(80):
        t.put(k, k)
    t.snapshot("hold")
    pinned = set(t._snapshots["hold"].pinned)
    frees_before = store.frees
    # 大量覆盖写: 旧版本页被快照钉住, 一个都不能回收
    for k in range(80):
        t.put(k, k + 1000)
    still_alive = pinned & set(store._pages)
    assert still_alive == pinned, "快照钉住的页被提前回收!"
    assert store.frees == frees_before or all(
        p in store._pages for p in pinned)
    print("  - 快照持有期间 %d 个钉住页全部存活(延迟回收)" % len(pinned))
    n = t.release("hold")
    assert n > 0
    assert pinned.isdisjoint(set(store._pages)), "释放后仍有钉住页残留"
    print("  - release 批量回收 %d 页, 钉住页全部释放" % n)


if __name__ == "__main__":
    print("[毒化检疫对抗测试]")
    test_quarantine_catches_dangling_access()
    test_double_unref_detected()
    test_pid_never_reused()
    test_snapshot_prevents_reclaim_then_bulk_free()
    print("全部毒化测试 PASS")
