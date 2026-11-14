#!/usr/bin/env python3
"""数仓任务依赖与回填计划器：增量维护的稳定拓扑序。

用法：
    python3 topo.py ops.json output.json

输入：
    {"n": 100, "ops": [{"op": "add", "a": 0, "b": 1}, {"op": "del", "a": 0, "b": 1}]}

输出：
    {"order": [...], "rejected": [{"op": ..., "cycle": [...]}],
     "stats": {"affected_sum": 整数, "recomputes": 整数}, "valid": true}

语义
----
节点 0..n-1 是数据任务，边 a->b 表示 b 需要 a 的分区产物，a 必须先执行。
重复添加已有的边、删除不存在的边都是空操作。

执行顺序
--------
level[v] = 从某个源任务到 v 的最长依赖链边数（源任务 level = 0）。
对每条现存边 a->b 必有 level[b] >= level[a] + 1，所以按 (level, 编号)
升序排列得到的序列

    * 是合法的全量拓扑序：上游一定排在被它喂数的下游之前；
    * 每个任务恰好出现一次；
    * 只取决于「最终依赖图」本身，与边以什么顺序接入无关，因此同一最终
      依赖图在不同接入顺序下会得到完全相同的计划；
    * 对局部拓扑变化只影响一小片：插入/删除一条边只会改到相关任务的
      level，其余任务的相对位置不变（天然稳定）。

增量维护（只扫描可能受影响的任务）
----------------------------------
* 加边 a->b（不成环）：只有 b 及其下游的 level 可能变高，沿后继做一次
  抬升传播，绝不触碰上游与无关任务。
* 删边 a->b：若 level[a] + 1 < level[b]，说明 b 的最长链根本不经过这条
  边，O(1) 判定「计划不变」直接返回；否则从 b 起按前驱重算 level，只有
  level 真的下降时才继续向下游传播。
* 加边成环：不改动图（保留旧图），返回被拒操作与完整环路径 [a, b, ..., a]
  ——首尾相同、以新边开头、其余按现存依赖由 b 回到 a。

统计
----
stats.affected_sum：累计实际检查/展开的受影响任务节点数。
stats.recomputes：实际全量重算次数。本实现在空图上增量构建并全程维护，
输出计划时只按 (level, 编号) 归并一次，不重算依赖，所以恒为 0。
"""

import json
import sys

__all__ = ["IncrementalPlanner", "run", "topo"]


def topo(adj, n):
    """兼容旧接口：对给定邻接表返回规范拓扑序。

    adj 形如 {a: [b, ...]}（a 必须在 b 之前）。返回的顺序与
    IncrementalPlanner.order() 完全相同，只取决于图本身。
    """
    planner = IncrementalPlanner(n)
    for a in adj:
        if 0 <= a < n:
            for b in adj[a]:
                if 0 <= b < n:
                    planner.add_edge(a, b)
    return planner.order()


class IncrementalPlanner:
    """维护依赖图、各任务 level，以及按层分桶的有序计划。"""

    __slots__ = ("n", "succ", "pred", "level", "buckets", "affected", "recomputes")

    def __init__(self, n):
        self.n = max(0, int(n))
        self.succ = [set() for _ in range(self.n)]   # a -> 依赖 a 产物的下游
        self.pred = [set() for _ in range(self.n)]   # b -> b 依赖的上游
        self.level = [0] * self.n                    # 最长依赖链长度
        self.buckets = [set(range(self.n))]          # buckets[level] = 这一层的任务
        self.affected = 0                            # 实际检查的受影响节点数
        self.recomputes = 0                          # 全量重算次数

    # ---------------------------------------------------------------- 内部工具

    def _set_level(self, v, new_level):
        old = self.level[v]
        if old == new_level:
            return
        self.buckets[old].discard(v)
        buckets = self.buckets
        while len(buckets) <= new_level:
            buckets.append(set())
        buckets[new_level].add(v)
        self.level[v] = new_level

    def _level_from_preds(self, v):
        """按现存前驱重算 v 的 level，并记账扫描过的节点与前驱。"""
        preds = self.pred[v]
        self.affected += 1 + len(preds)
        lv = self.level
        best = 0
        for u in preds:
            cand = lv[u] + 1
            if cand > best:
                best = cand
        return best

    def _raise(self, v, at_least):
        """把 v 抬到至少 at_least，并沿后继传播（level 单调增）。"""
        lv = self.level
        self.affected += 1
        if lv[v] >= at_least:
            return
        self._set_level(v, at_least)
        stack = [v]
        while stack:
            u = stack.pop()
            need = lv[u] + 1
            for w in self.succ[u]:
                self.affected += 1
                if lv[w] < need:
                    self._set_level(w, need)
                    stack.append(w)

    def _lower(self, v, new_level):
        """把 v 降到 new_level，并沿后继传播（level 单调减）。"""
        if new_level >= self.level[v]:
            return
        self._set_level(v, new_level)
        stack = [v]
        while stack:
            u = stack.pop()
            for w in self.succ[u]:
                cand = self._level_from_preds(w)
                if cand < self.level[w]:
                    self._set_level(w, cand)
                    stack.append(w)

    def _find_path(self, src, dst):
        """在现存依赖里找 src -> ... -> dst 的路径，没有则返回 None。

        任何 src->dst 路径上的内部节点 u 都满足 level[u] < level[dst]，
        因此只展开 level < level[dst] 的节点既不丢解又能大幅剪枝。
        """
        limit = self.level[dst]
        parent = {src: None}
        stack = [src]
        while stack:
            u = stack.pop()
            self.affected += 1
            if u == dst:
                path = []
                while u is not None:
                    path.append(u)
                    u = parent[u]
                path.reverse()
                return path
            for w in self.succ[u]:
                if w in parent:
                    continue
                if w != dst and self.level[w] >= limit:
                    continue
                parent[w] = u
                stack.append(w)
        return None

    # ---------------------------------------------------------------- 对外操作

    def add_edge(self, a, b):
        """加边 a->b。

        成功返回 None；若会成环，则不改图并返回完整环路径 [a, b, ..., a]。
        """
        if b in self.succ[a]:                 # 重复添加：空操作
            return None
        if a == b:                            # 自环
            return [a, a]
        # 加边后成环 <=> 现存图里 b 可达 a。若 b 可达 a，则必有
        # level[a] >= level[b] + 1，所以 level[b] >= level[a] 时可以安全跳过搜索。
        if self.level[b] < self.level[a]:
            path = self._find_path(b, a)
            if path is not None:
                return [a] + path             # 以新边开头，按现存依赖回到 a
        self.succ[a].add(b)
        self.pred[b].add(a)
        self._raise(b, self.level[a] + 1)
        return None

    def del_edge(self, a, b):
        """删边 a->b；删除不存在的边是空操作。"""
        if b not in self.succ[a]:
            return
        self.succ[a].discard(b)
        self.pred[b].discard(a)
        self.affected += 1
        if self.level[a] + 1 < self.level[b]:
            return                            # 该边不在 b 的最长链上：计划不变
        self._lower(b, self._level_from_preds(b))

    # ---------------------------------------------------------------- 输出

    def order(self):
        """按 (level, 编号) 归并出计划；无需重新计算依赖。"""
        buckets = self.buckets
        while len(buckets) > 1 and not buckets[-1]:
            buckets.pop()
        out = []
        for bucket in buckets:
            if bucket:
                out.extend(sorted(bucket))
        return out

    def validate(self, order):
        """校验：恰好包含全部任务各一次，且每条边的上游都在下游之前。"""
        if len(order) != self.n:
            return False
        pos = [0] * self.n
        for i, v in enumerate(order):
            pos[v] = i
        for u in range(self.n):
            pu = pos[u]
            for v in self.succ[u]:
                if pos[v] <= pu:
                    return False
        return True


def run(sc):
    try:
        n = int(sc.get("n", 0))
    except (TypeError, ValueError):
        n = 0
    planner = IncrementalPlanner(n)
    rejected = []
    for op in sc.get("ops") or []:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        try:
            a = int(op["a"])
            b = int(op["b"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= a < planner.n and 0 <= b < planner.n):
            continue
        if kind == "add":
            cycle = planner.add_edge(a, b)
            if cycle is not None:
                rejected.append({"op": op, "cycle": cycle})
        elif kind == "del":
            planner.del_edge(a, b)
    order = planner.order()
    return {
        "order": order,
        "rejected": rejected,
        "stats": {"affected_sum": planner.affected, "recomputes": planner.recomputes},
        "valid": planner.validate(order),
    }


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("usage: topo.py <ops.json> <output.json>\n")
        raise SystemExit(2)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        sc = json.load(f)
    result = run(sc)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)


if __name__ == "__main__":
    main()

