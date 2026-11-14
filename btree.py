#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
页式 MVCC B+ 树: 引用计数页回收 + COW 分裂/合并 + 快照固定迭代。

核心机制
========
1. PageStore 是页分配器:
   - 每个页有独立 pid(永不复用), 带引用计数 refcnt;
   - free 的页进入 quarantine(毒化区), 任何后续 access/unref 都记为悬挂访问(UAF);
   - 同时统计 allocs/frees/peak/deferred(已脱离活树但被快照钉住的页)。
2. B+ 树写入全程 COW: 沿根->叶复制受影响路径, 旧版本页由引用计数决定生死——
   没有快照钉住时立即回收, 有快照时延迟到 release 批量回收。
3. 快照 = 对当时可达页集合逐个 ref() 钉住; 迭代器只遍历该快照钉住的页,
   与后续写入完全隔离; release 时批量 unref, 引用归零的页立即回收。

CLI(兼容 README 约定): python3 btree.py ops.json output.json
ops: put/del/snap/release/iter, 其中 iter 必须指定 snap_id, 不再回退活页。
"""
import bisect
import json
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

PAGE_CAP = 4          # 每个页最多 4 个键(README 约定)
MIN_LEAF_KEYS = 2     # 叶页最少键数(根豁免)
MIN_INNER_KEYS = 2    # 内页最少键数 => 最少 3 个孩子(根豁免)
LEAF = "leaf"
INNER = "inner"
SAME = object()       # 递归删除返回值: 子树未变化


class DanglingPageError(RuntimeError):
    """访问已回收(毒化)或不存在的页。"""


class RefcountError(RuntimeError):
    """引用计数归零后继续 unref 等计数违约。"""


@dataclass
class Page:
    pid: int
    kind: str
    keys: List[Any] = field(default_factory=list)
    vals: List[Any] = field(default_factory=list)      # 仅叶页
    children: List[int] = field(default_factory=list)  # 仅内页, 存 pid
    refcnt: int = 0


class PageStore:
    """带引用计数 / 毒化检疫 / 完整统计的页分配器(线程安全)。"""

    def __init__(self) -> None:
        self._mu = threading.RLock()
        self._pages: Dict[int, Page] = {}   # 仅存活页
        self._quarantine: set = set()       # 已回收 pid, 永不复用
        self._next_pid = 1
        self.alloc_stash: Optional[set] = None   # 当前写事务新分配 pid 集合
        # 统计
        self.allocs = 0
        self.frees = 0
        self.peak_live = 0
        self.deferred = 0                   # 当前脱离活树但被钉住的页
        self.deferred_peak = 0
        # 安全工具统计(对应验收中的“工具统计为零”)
        self.uaf_violations = 0             # 悬挂页访问次数
        self.double_free = 0                # 对已回收页再次 unref
        self.refcount_violations = 0        # 引用计数为负等
        self.violation_log: List[str] = []

    # ---- 基础分配 / 访问 -------------------------------------------------
    def alloc(self, kind: str) -> Page:
        with self._mu:
            pid = self._next_pid
            self._next_pid += 1
            page = Page(pid=pid, kind=kind)
            self._pages[pid] = page
            self.allocs += 1
            if len(self._pages) > self.peak_live:
                self.peak_live = len(self._pages)
            if self.alloc_stash is not None:
                self.alloc_stash.add(pid)    # 归属当前写事务, 供提交判定
            return page

    def access(self, pid: int) -> Page:
        """读/写页面前必经的守卫: 命中毒化区即记 UAF 并抛错。"""
        with self._mu:
            page = self._pages.get(pid)
            if page is None:
                self.uaf_violations += 1
                where = "quarantine" if pid in self._quarantine else "unknown"
                msg = "UAF: access pid=%r (%s)" % (pid, where)
                self.violation_log.append(msg)
                raise DanglingPageError(msg)
            return page

    def ref(self, pid: int) -> Page:
        page = self.access(pid)
        with self._mu:
            page.refcnt += 1
            return page

    def unref(self, pid: int) -> None:
        with self._mu:
            page = self._pages.get(pid)
            if page is None:
                # 对已回收/未知页 unref 本身就是悬挂操作
                self.uaf_violations += 1
                self.double_free += 1
                where = "quarantine" if pid in self._quarantine else "unknown"
                msg = "double-free/unref pid=%d (%s)" % (pid, where)
                self.violation_log.append(msg)
                raise DanglingPageError(msg)
            page.refcnt -= 1
            if page.refcnt < 0:
                self.refcount_violations += 1
                msg = "refcount<0 pid=%d" % pid
                self.violation_log.append(msg)
                raise RefcountError(msg)
            if page.refcnt == 0:
                self._free_locked(page)

    def _free_locked(self, page: Page) -> None:
        del self._pages[page.pid]
        self._quarantine.add(page.pid)   # 毒化: 永不复用 pid
        self.frees += 1

    def live_count(self) -> int:
        with self._mu:
            return len(self._pages)

    def snapshot_stats(self) -> Dict[str, int]:
        with self._mu:
            return dict(
                allocs=self.allocs,
                frees=self.frees,
                pages_live=len(self._pages),
                peak=self.peak_live,
                deferred_now=self.deferred,
                deferred_peak=self.deferred_peak,
                quarantined=len(self._quarantine),
                uaf_violations=self.uaf_violations,
                double_free=self.double_free,
                refcount_violations=self.refcount_violations,
            )


class RWLock:
    """读写锁: 多读者并发, 写者独占, 用于读写事务交错。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False

    def r_acquire(self) -> None:
        with self._cond:
            while self._writer:
                self._cond.wait()
            self._readers += 1

    def r_release(self) -> None:
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def w_acquire(self) -> None:
        self._cond.acquire()
        while self._writer or self._readers > 0:
            self._cond.wait()
        self._writer = True

    def w_release(self) -> None:
        self._writer = False
        self._cond.notify_all()
        self._cond.release()

    class Guard:
        def __init__(self, lock: "RWLock", write: bool) -> None:
            self.lock, self.write = lock, write

        def __enter__(self):
            (self.lock.w_acquire if self.write else self.lock.r_acquire)()
            return self

        def __exit__(self, *exc):
            (self.lock.w_release if self.write else self.lock.r_release)()

    def read(self) -> Guard:
        return RWLock.Guard(self, False)

    def write(self) -> Guard:
        return RWLock.Guard(self, True)


@dataclass
class Snapshot:
    sid: Any
    root_pid: int
    pinned: List[int]          # 快照钉住的全部页(各持 1 个引用)
    key_count: int


class MVCCBTree:
    """引用计数 + COW 的多版本 B+ 树。"""

    def __init__(self, store: Optional[PageStore] = None) -> None:
        self.store = store or PageStore()
        self._rw = RWLock()
        self._snapshots: Dict[Any, Snapshot] = {}
        root = self.store.alloc(LEAF)          # 空叶作为初始根
        self.store.ref(root.pid)               # 树根持有 1 个引用
        self.root_pid = root.pid

    # ===================================================================
    # 页构造 / 销毁工具
    #
    # 核心不变量(只看树侧贡献, 快照贡献另计):
    #   refcnt_tree(P) = 1  当且仅当 P 从当前根可达; 否则为 0。
    # 即每个可达页恰持 1 个树引用, 根也不例外(不再单列“根所有权”)。
    #
    # 实现方式 —— 可达性增量提交:
    #   * 所有新分配页(叶/内) refcnt 恒为 0, 是“待挂接”页, 构造期
    #     分裂/合并/借用一律不碰任何 refcnt;
    #   * 一次写入构造出新根后, _commit_root 比较新旧根可达集:
    #       旧可达 - 新可达 = 被替换/合并掉的旧页 -> unref(无快照即回收);
    #       新可达 - 旧可达 = 新分配且仍可达的页 -> ref;
    #       交集 = 共享页, 计数不动(COW 共享)。
    #   * 快照在可达集上额外 +1, release 时 -1; 页是否延迟回收完全由
    #     refcnt 是否归零决定, 写入路径无需感知快照。
    # pid 全局永不复用, 故两个差集天然精确, 无需逐边配对。
    # ===================================================================
    def _clone_leaf(self, src: Page) -> Page:
        p = self.store.alloc(LEAF)
        p.keys = list(src.keys)
        p.vals = list(src.vals)
        return p

    def _make_inner(self, keys: List[Any], children: List[int]) -> Page:
        page = self.store.alloc(INNER)
        page.keys = list(keys)
        page.children = list(children)   # 待挂接, 零引用, 不 ref 孩子
        return page

    def _set_child(self, inner: Page, slot: int, new_pid: int) -> None:
        """构造期在待挂接内页上换孩子: 纯指针替换, 不碰引用计数。"""
        inner.children[slot] = new_pid

    def _reachable(self, root_pid: int) -> List[int]:
        out: List[int] = []
        stack = [root_pid]
        while stack:
            pid = stack.pop()
            page = self.store.access(pid)      # 毒化守卫
            out.append(pid)
            if page.kind == INNER:
                stack.extend(page.children)
        return out

    def _commit_root(self, new_pid: int) -> None:
        """可达性增量提交: 把树侧引用从旧根切换到新根。

        树侧引用不变量: 每个“当前根可达”的页恰持 1 个树引用; refcnt=0
        的页绝不允许残留在存活表里。提交分三步:
          1. 本次新分配(stash)且新根可达 -> 建立 1 个树引用(ref);
          2. 旧根可达但新根不可达(被替换/合并消化的旧页) -> 释放树引用,
             有快照钉住则 refcnt 不归零, 自然延迟到 release;
          3. 本次新分配但最终不可达的“构造期垃圾”(如深层合并中被消化的
             克隆内页) -> 递归释放, 避免零计数僵尸页及其悬空子指针。
        pid 全局永不复用, stash 精确归属本次事务, 三类互斥无歧义。
        """
        stash = self.store.alloc_stash or set()
        old_set = set(self._reachable(self.root_pid))
        new_reach = self._reachable(new_pid)
        new_set = set(new_reach)
        for pid in new_reach:
            if pid in stash:                  # 1. 新页进入活树
                self.store.ref(pid)
        for pid in old_set - new_set:         # 2. 旧页脱离活树
            self.store.unref(pid)
        for pid in list(stash):               # 3. 构造期垃圾回收
            if pid not in new_set and pid in self.store._pages:
                self._discard_garbage(pid, new_set)
        self.root_pid = new_pid
        self.store.alloc_stash = None

    def _discard_garbage(self, pid: int, live_set: set) -> None:
        """释放一棵确认未挂活树的垃圾子树(入参为本次 stash 的新页)。

        live_set 是新根全部可达页。任何在 live_set 中的页仍被活树引用
        (可能同时被某垃圾内页残留指针指向), 一律受保护不得释放 —— 这是
        可达性记账相对逐边配对的关键防护: 只认最终可达身份。
          * 同为本次 stash 且不在 live_set 的孩子 => 同为垃圾, 递归;
          * 在 live_set(活树共享)或为旧页的孩子 => 不触碰, 旧页由步骤2处理。
        """
        if pid in live_set:
            return                            # 仍被活树引用: 受保护
        stash = self.store.alloc_stash or set()
        page = self.store._pages.get(pid)
        if page is None:
            return
        if page.kind == INNER:
            for cpid in list(page.children):
                if cpid in stash:
                    self._discard_garbage(cpid, live_set)
        if page.refcnt == 0:
            self.store._free_locked(page)
        else:
            self.store.unref(pid)

    def _sample_deferred(self) -> None:
        """统计脱离活树但仍被快照钉住的页(延迟回收内存上界证据)。"""
        reach = set(self._reachable(self.root_pid))
        d = sum(1 for pid in self.store._pages if pid not in reach)
        self.store.deferred = d
        self.store.deferred_peak = max(self.store.deferred_peak, d)

    # ===================================================================
    # 写入路径: 全程 COW
    # ===================================================================
    def put(self, key: Any, val: Any) -> None:
        with self._rw.write():
            self.store.alloc_stash = set()
            result = self._insert(self.root_pid, key, val)
            if result[0] == "same":
                self.store.alloc_stash = None
                return
            if result[0] == "one":
                new_pid = result[1]
            else:  # 根分裂: 新内页承载提升键
                _, left_pid, sep, right_pid = result
                new_pid = self._make_inner([sep], [left_pid, right_pid]).pid
            self._commit_root(new_pid)
            self._sample_deferred()

    def _insert(self, pid: int, key: Any, val: Any) -> Tuple:
        page = self.store.access(pid)
        if page.kind == LEAF:
            pos = bisect.bisect_left(page.keys, key)
            if pos < len(page.keys) and page.keys[pos] == key:
                if page.vals[pos] == val:
                    return ("same", pid)
                nl = self._clone_leaf(page)    # COW 改值
                nl.vals[pos] = val
                return ("one", nl.pid)
            nl = self._clone_leaf(page)
            nl.keys.insert(pos, key)
            nl.vals.insert(pos, val)
            if len(nl.keys) <= PAGE_CAP:
                return ("one", nl.pid)
            # 叶分裂: 5 键 -> 2 + 3
            mid = len(nl.keys) // 2
            left = self.store.alloc(LEAF)
            left.keys, left.vals = nl.keys[:mid], nl.vals[:mid]
            right = self.store.alloc(LEAF)
            right.keys, right.vals = nl.keys[mid:], nl.vals[mid:]
            # nl 是尚未挂树的临时克隆, 直接回收(无引用者, 不进快照)
            self.store._free_locked(nl)
            return ("split", left.pid, right.keys[0], right.pid)

        slot = bisect.bisect_right(page.keys, key)
        result = self._insert(page.children[slot], key, val)
        if result[0] == "same":
            return result
        if result[0] == "one":
            children = list(page.children)
            children[slot] = result[1]
            ni = self._make_inner(page.keys, children)
            return ("one", ni.pid)
        # 孩子分裂, 提升键插入本层
        _, left_pid, sep, right_pid = result
        keys2 = page.keys[:slot] + [sep] + page.keys[slot:]
        children2 = (page.children[:slot] + [left_pid, right_pid]
                     + page.children[slot + 1:])
        if len(keys2) <= PAGE_CAP:
            return ("one", self._make_inner(keys2, children2).pid)
        # 内页分裂: 5 键 6 孩 -> 2/提升/2
        m = PAGE_CAP // 2
        promoted = keys2[m]
        left = self._make_inner(keys2[:m], children2[:m + 1])
        right = self._make_inner(keys2[m + 1:], children2[m + 1:])
        return ("split", left.pid, promoted, right.pid)

    def delete(self, key: Any) -> None:
        with self._rw.write():
            self.store.alloc_stash = set()
            new_pid = self._delete(self.root_pid, key)
            if new_pid is SAME:
                self.store.alloc_stash = None
                return
            root = self.store.access(new_pid)
            # 根内页只剩一个孩子 => 塌缩一层
            if root.kind == INNER and len(root.children) == 1:
                new_pid = root.children[0]
            self._commit_root(new_pid)
            self._sample_deferred()

    def _delete(self, pid: int, key: Any):
        page = self.store.access(pid)
        if page.kind == LEAF:
            pos = bisect.bisect_left(page.keys, key)
            if pos >= len(page.keys) or page.keys[pos] != key:
                return SAME
            nl = self._clone_leaf(page)
            nl.keys.pop(pos)
            nl.vals.pop(pos)
            return nl.pid                      # 可能欠载, 由父级修复

        slot = bisect.bisect_right(page.keys, key)
        new_child = self._delete(page.children[slot], key)
        if new_child is SAME:
            return SAME
        children = list(page.children)
        children[slot] = new_child
        ni = self._make_inner(page.keys, children)   # 全新未共享, 可直接改
        fixed = self._rebalance(ni.pid, slot)
        # 关键: 删除可能改变某叶/子树的最小键(删首键、合并、借用),
        # 祖先内页缓存的分隔符必须按孩子实际最小键重算, 否则会残留指向
        # 已删除键的旧分隔符, 破坏“分隔键 == 右孩子最小键”的 B+ 树不变量。
        self._refresh_separators(fixed)
        return fixed

    def _subtree_min_key(self, pid: int) -> Any:
        """取一棵待挂接子树的最小键(沿最左孩子下降到叶首键)。"""
        node = self.store.access(pid)
        while node.kind == INNER:
            node = self.store.access(node.children[0])
        if not node.keys:
            return None
        return node.keys[0]

    def _refresh_separators(self, pid: int) -> None:
        """自底向上把内页分隔键重算为“右孩子的实际最小键”(纯结构, 零引用改动)。"""
        node = self.store.access(pid)
        if node.kind != INNER:
            return
        for cpid in node.children:
            self._refresh_separators(cpid)
        node.keys = [self._subtree_min_key(cpid) for cpid in node.children[1:]]

    def _underflow(self, page: Page) -> bool:
        if page.kind == LEAF:
            return len(page.keys) < MIN_LEAF_KEYS
        return len(page.keys) < MIN_INNER_KEYS

    def _can_lend(self, pid: int) -> bool:
        p = self.store.access(pid)
        if p.kind == LEAF:
            return len(p.keys) > MIN_LEAF_KEYS
        return len(p.keys) > MIN_INNER_KEYS

    def _rebalance(self, node_pid: int, i: int) -> int:
        node = self.store.access(node_pid)
        child = self.store.access(node.children[i])
        if not self._underflow(child):
            return node_pid
        if i > 0 and self._can_lend(node.children[i - 1]):
            self._borrow_left(node, i)
            return node_pid
        if i + 1 < len(node.children) and self._can_lend(node.children[i + 1]):
            self._borrow_right(node, i)
            return node_pid
        # 无人可借 => 合并; 优先与右兄弟, 否则左兄弟
        if i + 1 < len(node.children):
            return self._merge(node, i, i + 1)
        return self._merge(node, i - 1, i)

    def _borrow_left(self, node: Page, i: int) -> None:
        left = self.store.access(node.children[i - 1])
        child = self.store.access(node.children[i])
        if child.kind == LEAF:
            nl = self._clone_leaf(left)
            k, v = nl.keys.pop(), nl.vals.pop()
            nc = self._clone_leaf(child)
            nc.keys.insert(0, k)
            nc.vals.insert(0, v)
            self._set_child(node, i - 1, nl.pid)
            self._set_child(node, i, nc.pid)
            node.keys[i - 1] = nc.keys[0]
        else:
            moved = left.children[-1]
            nl = self._make_inner(left.keys[:-1], left.children[:-1])
            nc = self._make_inner(
                [left.keys[-1]] + child.keys,
                [moved] + child.children,
            )
            self._set_child(node, i - 1, nl.pid)
            self._set_child(node, i, nc.pid)
            node.keys[i - 1] = left.keys[-1]

    def _borrow_right(self, node: Page, i: int) -> None:
        child = self.store.access(node.children[i])
        right = self.store.access(node.children[i + 1])
        if child.kind == LEAF:
            nr = self._clone_leaf(right)
            k, v = nr.keys.pop(0), nr.vals.pop(0)
            nc = self._clone_leaf(child)
            nc.keys.append(k)
            nc.vals.append(v)
            self._set_child(node, i, nc.pid)
            self._set_child(node, i + 1, nr.pid)
            node.keys[i] = nr.keys[0]
        else:
            moved = right.children[0]
            nc = self._make_inner(
                child.keys + [node.keys[i]],
                child.children + [moved],
            )
            nr = self._make_inner(right.keys[1:], right.children[1:])
            self._set_child(node, i, nc.pid)
            self._set_child(node, i + 1, nr.pid)
            node.keys[i] = right.keys[0]

    def _merge(self, node: Page, li: int, ri: int) -> int:
        left = self.store.access(node.children[li])
        right = self.store.access(node.children[ri])
        if left.kind == LEAF:
            merged = self.store.alloc(LEAF)
            merged.keys = left.keys + right.keys
            merged.vals = left.vals + right.vals
        else:
            merged = self._make_inner(
                left.keys + [node.keys[li]] + right.keys,
                left.children + right.children,
            )
        node.keys.pop(li)
        node.children.pop(ri)               # 待挂接页上摘右兄弟(不碰计数)
        self._set_child(node, li, merged.pid)
        return node.pid

    # ===================================================================
    # 快照 / 迭代
    # ===================================================================
    def snapshot(self, sid: Any) -> Snapshot:
        with self._rw.read():
            pinned = self._reachable(self.root_pid)
            for pid in pinned:
                self.store.ref(pid)           # 快照对每页持 1 引用
            root = self.store.access(self.root_pid)
            snap = Snapshot(sid, self.root_pid, pinned,
                            self._count_leaves_keys(root))
            self._snapshots[sid] = snap
            return snap

    def release(self, sid: Any) -> int:
        """释放快照: 批量 unref 钉住的页, 返回本次实际回收页数。"""
        with self._rw.write():
            snap = self._snapshots.pop(sid)
            before = self.store.frees
            for pid in snap.pinned:           # 批量回收
                self.store.unref(pid)
            self._sample_deferred()
            return self.store.frees - before

    def has_snapshot(self, sid: Any) -> bool:
        return sid in self._snapshots

    def _count_leaves_keys(self, root: Page) -> int:
        total = 0
        stack = [root.pid]
        while stack:
            p = self.store.access(stack.pop())
            if p.kind == LEAF:
                total += len(p.keys)
            else:
                stack.extend(p.children)
        return total

    def iter_snapshot(self, sid: Any) -> List[Tuple[Any, Any]]:
        """固定到快照版本的一致键序迭代(只触碰该快照钉住的页)。

        不做“叶首键排序”——COW 分裂会令不同版本叶范围在首键上重叠。
        改为从快照根做中序树下降: 内页按孩子槽位顺序递归, 叶页按槽内
        顺序输出, 这正是 B+ 树定义的全局键序。快照页集合不可变, 遍历期
        任何写入都不影响结果; 每次访问都经毒化守卫, 命中已回收页即记
        UAF 并抛错(压测中该计数必须恒为 0)。
        """
        with self._rw.read():
            if sid not in self._snapshots:
                raise KeyError("snapshot %r not found" % (sid,))
            snap = self._snapshots[sid]
            pinned = set(snap.pinned)

        items: List[Tuple[Any, Any]] = []
        leaf_pids: List[int] = []

        def descend(pid: int) -> None:
            if pid not in pinned:
                raise AssertionError("iterator escaped snapshot at %d" % pid)
            node = self.store.access(pid)       # 毒化守卫
            if node.kind == LEAF:
                leaf_pids.append(pid)
                for k, v in zip(node.keys, node.vals):
                    items.append((k, v))
            else:
                for cpid in node.children:      # 槽位顺序 = 键序
                    descend(cpid)

        descend(snap.root_pid)

        # 一致性校验: 键全局严格有序、数量吻合、恰好覆盖全部快照叶
        for i in range(1, len(items)):
            if not (items[i - 1][0] < items[i][0]):
                raise AssertionError("snapshot key order violated at %d" % i)
        if len(items) != snap.key_count:
            raise AssertionError(
                "snapshot key count mismatch: got %d want %d"
                % (len(items), snap.key_count))
        expect_leaves = set(p for p in snap.pinned
                            if self.store.access(p).kind == LEAF)
        if set(leaf_pids) != expect_leaves or len(leaf_pids) != len(expect_leaves):
            raise AssertionError("iterator did not cover exactly snapshot leaves")
        return items

    def scan_live(self) -> List[Tuple[Any, Any]]:
        """活树扫描: 用临时快照固定, 扫完即放。"""
        sid = object()
        self.snapshot(sid)
        try:
            return self.iter_snapshot(sid)
        finally:
            self.release(sid)

    # ===================================================================
    # 不变量自检(压测用)
    # ===================================================================
    def check_refcount_invariants(self) -> None:
        with self._rw.read():
            reachable = set(self._reachable(self.root_pid))
            # 结构完整性只在“当前活树”上要求: 可达内页的孩子必须存活且可达。
            # 快照钉住的旧版本子树不可达是正常现象, 不参与该校验。
            for pid in reachable:
                page = self.store._pages[pid]
                if page.kind == INNER:
                    for c in page.children:
                        if c not in self.store._pages:
                            raise AssertionError("dangling child ptr -> %d" % c)
                        if c not in reachable:
                            raise AssertionError(
                                "unreachable child %d of reachable page %d" % (c, pid))
            # 快照子系统自洽: 每个钉住的页都存活, 且其内部指针也都存活。
            for snap in self._snapshots.values():
                pin = set(snap.pinned)
                for pid in snap.pinned:
                    if pid not in self.store._pages:
                        raise AssertionError("snapshot pins freed page %d" % pid)
                    sp = self.store._pages[pid]
                    if sp.kind == INNER:
                        for c in sp.children:
                            if c not in pin or c not in self.store._pages:
                                raise AssertionError(
                                    "snapshot subtree broken at %d -> %d" % (pid, c))
            # 计数 = 树侧(可达=1) + 每个快照钉住的 1
            expected = dict((pid, 1 if pid in reachable else 0)
                            for pid in self.store._pages)
            for snap in self._snapshots.values():
                for pid in snap.pinned:
                    expected[pid] += 1
            for pid, want in expected.items():
                got = self.store._pages[pid].refcnt
                if got != want:
                    raise AssertionError(
                        "refcnt mismatch pid=%d: got %d want %d" % (pid, got, want))
            # 不允许 refcnt=0 的页残留在存活表(僵尸页)
            zombies = [pid for pid, p in self.store._pages.items() if p.refcnt == 0]
            if zombies:
                raise AssertionError("zero-refcnt live pages: %s" % zombies[:8])

    def stats(self) -> Dict[str, Any]:
        s = self.store.snapshot_stats()
        s["snapshots_held"] = len(self._snapshots)
        s["snapshot_pinned"] = sum(len(x.pinned) for x in self._snapshots.values())
        s["reclaim_rate"] = (s["frees"] / s["allocs"]) if s["allocs"] else 1.0
        return s

    def destroy(self) -> None:
        """丢弃整棵活树的树侧引用; 仍被快照钉住的页继续存活。"""
        with self._rw.write():
            if self.root_pid is not None:
                old_pid = self.root_pid
                for pid in self._reachable(old_pid):
                    self.store.unref(pid)   # 每个可达页释放那 1 个树引用
                self.root_pid = None  # type: ignore
                # 根已摘除: 剩余存活页必然是快照钉住的延迟回收页
                self.store.deferred = len(self.store._pages)
                self.store.deferred_peak = max(
                    self.store.deferred_peak, self.store.deferred)


# =======================================================================
# CLI 兼容层
# =======================================================================
def run(ops: List[dict]) -> dict:
    store = PageStore()
    tree = MVCCBTree(store)
    iters: Dict[str, list] = {}
    errors: List[str] = []
    batch_reclaims: List[dict] = []

    for o in ops:
        op = o.get("op")
        try:
            if op == "put":
                tree.put(o["k"], o["v"])
            elif op == "del":
                tree.delete(o["k"])
            elif op == "snap":
                tree.snapshot(o["id"])
            elif op == "release":
                n = tree.release(o["id"])
                batch_reclaims.append(dict(id=o["id"], reclaimed=n))
            elif op == "iter":
                sid = o.get("snap_id", o.get("snap"))
                if sid is None or not tree.has_snapshot(sid):
                    errors.append("iter requires live snap_id, got=%r" % (sid,))
                    continue
                items = tree.iter_snapshot(sid)
                name = str(o.get("out", sid))
                iters[name] = [k for k, _ in items]
            else:
                errors.append("unknown op: %r" % (op,))
        except Exception as exc:  # 驱动层保留现场
            errors.append("%s: %s: %s" % (op, type(exc).__name__, exc))

    mid_stats = tree.stats()
    # 释放残留快照后应能 100% 回收
    for sid in list(tree._snapshots.keys()):
        tree.release(sid)
    tree.destroy()
    final = store.snapshot_stats()
    final["reclaim_rate"] = (final["frees"] / final["allocs"]) if final["allocs"] else 1.0
    return dict(
        pages_live=final["pages_live"],
        reclaimed=final["frees"],
        iters=iters,
        errors=errors,
        batch_reclaims=batch_reclaims,
        stats=dict(
            allocs=final["allocs"],
            frees=final["frees"],
            peak=final["peak"],
            deferred_peak=final["deferred_peak"],
            deferred_now=final["deferred_now"],
            uaf_violations=final["uaf_violations"],
            double_free=final["double_free"],
            refcount_violations=final["refcount_violations"],
            reclaim_rate=final["reclaim_rate"],
            mid_run=mid_stats,
        ),
    )


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python3 btree.py ops.json output.json", file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1]) as f:
        request = json.load(f)
    ops = request["ops"] if isinstance(request, dict) else request
    result = run(ops)
    with open(sys.argv[2], "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
