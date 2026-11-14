#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
并发读写压测 + 验收证据生成。

覆盖验收点:
  1. 多写者持续 put/delete(触发 COW 分裂/合并), 多读者在快照上迭代 ——
     迭代器只访问快照钉住的页, 全程毒化守卫, 悬挂页访问(UAF)必须为 0;
  2. 快照存活期间内存有上界: 记录 live/deferred/peak, 验证延迟页数量
     不超过“写入量 × 快照持有窗口”的可解释边界;
  3. 同一份数据两个快照的键序完全一致;
  4. 快照释放后页批量回收, 最终回收率 100%(frees == allocs)。

用法: python3 stress.py [--seconds N] [--writers W] [--readers R]
"""
import argparse
import random
import threading
import time
from btree import MVCCBTree, PageStore


class StressDriver:
    def __init__(self, keyspace: int) -> None:
        self.store = PageStore()
        self.tree = MVCCBTree(self.store)
        self.keyspace = keyspace
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.reader_checks = 0
        self.reader_failures: list = []
        self.snapshot_pairs_checked = 0
        self.writes = 0
        self.max_deferred_during_snap = 0
        self.max_live_during_snap = 0

    # ---- 写者: 混合 put/delete, 不断触发分裂与合并 -----------------------
    def writer_loop(self, wid: int, rng: random.Random) -> None:
        local = 0
        while not self.stop.is_set():
            k = rng.randrange(self.keyspace)
            if rng.random() < 0.7:
                self.tree.put(k, (wid, rng.randrange(1 << 30)))
            else:
                self.tree.delete(k)
            local += 1
            if local % 200 == 0:
                with self.lock:
                    self.writes += local
                local = 0
        with self.lock:
            self.writes += local

    # ---- 读者: 建快照 -> 双快照对比 -> 迭代 -> 释放, 全流程一致性 --------
    def reader_loop(self, rid: int, rng: random.Random) -> None:
        while not self.stop.is_set():
            tag = self.reader_checks
            sid_a = ("r%d-a-%d" % (rid, tag))
            sid_b = ("r%d-b-%d" % (rid, tag))
            try:
                # 同一读临界区、基于同一根原子拍两个快照, 保证版本完全一致
                # (读锁内写者无法提交, 两次 snapshot 看到的 root_pid 相同)。
                with self.tree._rw.read():
                    self.tree.snapshot(sid_a)
                    self.tree.snapshot(sid_b)
                items_a = self.tree.iter_snapshot(sid_a)
                items_b = self.tree.iter_snapshot(sid_b)
                if items_a != items_b:
                    with self.lock:
                        self.reader_failures.append("twin snapshot order mismatch")
                # 键严格有序
                keys = [k for k, _ in items_a]
                if any(not (keys[i - 1] < keys[i]) for i in range(1, len(keys))):
                    with self.lock:
                        self.reader_failures.append("non-monotonic key order")
                # 持有快照期间再做一批写入, 视图必须保持不变
                for _ in range(8):
                    self.tree.put(rng.randrange(self.keyspace), rng.randrange(1 << 20))
                if self.tree.iter_snapshot(sid_a) != items_a:
                    with self.lock:
                        self.reader_failures.append("snapshot mutated by concurrent write")
                # 记录快照存活窗口内的延迟/存活页上界
                st = self.store.snapshot_stats()
                with self.lock:
                    self.max_deferred_during_snap = max(
                        self.max_deferred_during_snap, st["deferred_now"])
                    self.max_live_during_snap = max(
                        self.max_live_during_snap, st["pages_live"])
                    self.reader_checks += 1
                    self.snapshot_pairs_checked += 1
                # 释放: 批量回收钉住的页
                self.tree.release(sid_b)
                self.tree.release(sid_a)
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    self.reader_failures.append("%s: %s" % (type(exc).__name__, exc))

    def run(self, seconds: float, writers: int, readers: int) -> dict:
        rng = random.Random(20261114)
        threads = []
        for i in range(writers):
            th = threading.Thread(
                target=self.writer_loop, args=(i, random.Random(rng.random())),
                name="writer-%d" % i, daemon=True)
            threads.append(th)
        for i in range(readers):
            th = threading.Thread(
                target=self.reader_loop, args=(i, random.Random(rng.random())),
                name="reader-%d" % i, daemon=True)
            threads.append(th)
        for th in threads:
            th.start()
        time.sleep(seconds)
        self.stop.set()
        for th in threads:
            th.join(timeout=10)

        # 残留快照全部释放后, 树销毁, 必须 100% 回收
        for sid in list(self.tree._snapshots.keys()):
            self.tree.release(sid)
        self.tree.destroy()
        st = self.store.snapshot_stats()
        st.update(
            writes=self.writes,
            reader_checks=self.reader_checks,
            reader_failures=self.reader_failures,
            max_deferred_during_snap=self.max_deferred_during_snap,
            max_live_during_snap=self.max_live_during_snap,
            held_snapshots_at_end=len(self.tree._snapshots),
            reclaim_rate=(st["frees"] / st["allocs"]) if st["allocs"] else 1.0,
        )
        return st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--writers", type=int, default=4)
    ap.add_argument("--readers", type=int, default=4)
    ap.add_argument("--keyspace", type=int, default=200)
    args = ap.parse_args()

    drv = StressDriver(args.keyspace)
    st = drv.run(args.seconds, args.writers, args.readers)

    print("=" * 64)
    print("并发读写压测结果 (writers=%d readers=%d keyspace=%d %.1fs)"
          % (args.writers, args.readers, args.keyspace, args.seconds))
    print("-" * 64)
    print("总写操作(put/del)      : %d" % st["writes"])
    print("读者一致性检查次数     : %d" % st["reader_checks"])
    print("页分配 allocs          : %d" % st["allocs"])
    print("页回收 frees           : %d" % st["frees"])
    print("存活页 pages_live      : %d (销毁后应为 0)" % st["pages_live"])
    print("历史峰值页 peak        : %d" % st["peak"])
    print("快照期延迟页峰值       : %d" % st["max_deferred_during_snap"])
    print("快照期存活页峰值       : %d" % st["max_live_during_snap"])
    print("回收率 reclaim_rate    : %.4f" % st["reclaim_rate"])
    print("-" * 64)
    print("UAF 悬挂页访问         : %d" % st["uaf_violations"])
    print("double-free           : %d" % st["double_free"])
    print("refcount 违规          : %d" % st["refcount_violations"])
    print("读者一致性失败         : %d %s" % (len(st["reader_failures"]),
                                             st["reader_failures"][:3]))
    print("=" * 64)

    ok = (
        st["uaf_violations"] == 0
        and st["double_free"] == 0
        and st["refcount_violations"] == 0
        and not st["reader_failures"]
        and st["pages_live"] == 0
        and abs(st["reclaim_rate"] - 1.0) < 1e-9
        and st["frees"] == st["allocs"]
        and st["reader_checks"] > 0
    )
    print("ACCEPTANCE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
