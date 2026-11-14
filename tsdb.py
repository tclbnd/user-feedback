#!/usr/bin/env python3
"""分层乱序时序缓冲（LSM 风格，修复基线实现）。

基线缺陷 -> 修复方式：
1. 无水位判断、迟到点混入常规层 -> 到达点按运行水位 max_ts - watermark 判定，
   超水位点只进补偿通道（late_buf/late_runs），永不回灌常规层；
2. 压实不去重、同键多版本并存 -> 所有 run 按 (key, ts) 有序，k 路归并时同
   (key, ts) 取 (ver, 到达序 seq) 最大者（等 ver 后到者胜），重复点计入统计；
3. 查询按写入序 -> L0 与各密封 run 做 heapq k 路归并，输出按 ts 升、同 ts
   ver 降，与离线全排序逐点一致，且结果只依赖点多重集、与写入顺序无关。

CLI 保持 README 契约：python3 tsdb.py request.json output.json
"""
import hashlib
import heapq
import json
import sys
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Point:
    ts: int
    key: str
    v: int
    ver: int
    seq: int = -1  # 全局到达序，仅用于等 ver 时的后到者决胜

    def as_dict(self):
        return {"ts": self.ts, "key": self.key, "v": self.v, "ver": self.ver}


def _sort_key(p):
    return p.key, p.ts


def merge_runs(runs):
    """对若干按 (key, ts) 有序的 run 做 k 路归并。

    同 (key, ts) 保留 (ver, seq) 最大的点；返回 (归并后 run, 被压掉的重复点数)。
    归约满足结合律：任意 run 划分、任意压实时机，最终结果唯一。
    """
    iters = [iter(r) for r in runs if r]
    if not iters:
        return [], 0
    out, removed = [], 0
    cur = None
    winner = None
    for p in heapq.merge(*iters, key=_sort_key):
        k = (p.key, p.ts)
        if k != cur:
            if winner is not None:
                out.append(winner)
            cur, winner = k, p
        else:
            if (p.ver, p.seq) > (winner.ver, winner.seq):
                winner = p
            removed += 1
    if winner is not None:
        out.append(winner)
    return out, removed


class Engine:
    """L0 内存缓冲 -> L1 密封有序 run 集 -> L2 无界有序 run 集；另设补偿通道。"""

    def __init__(self, watermark, levels):
        self.watermark = watermark
        self.cap0, self.cap1 = levels[0], levels[1]
        self.l0 = []              # 未密封的内存点（到达序）
        self.l1 = []              # list[run]，run 为按 (key,ts) 有序去重的 list[Point]
        self.l2 = []
        self.late_buf = []        # 补偿通道缓冲
        self.late_runs = []
        self.max_ts = None
        self.seq = 0
        self.rid = 0
        self.stats = {
            "in_regular": 0, "in_late": 0,
            "L0->L1": 0, "L1->L2": 0, "L2_major": 0,
            "late_seal": 0, "late_major": 0,
            "dup_removed": 0, "late_dup_removed": 0,
        }

    # ---------- 内部压实 ----------
    def _seal(self, pts):
        """把一批无序点排序去重，密封成一个不可变有序 run。"""
        run, removed = merge_runs([sorted(pts, key=_sort_key)])
        self.rid += 1
        return run, removed

    def _flush_l0(self):
        if not self.l0:
            return
        run, rm = self._seal(self.l0)
        self.l0.clear()
        self.l1.append(run)
        self.stats["L0->L1"] += 1
        self.stats["dup_removed"] += rm
        if self._size(1) > self.cap1:           # L1 超容量 -> 压实进 L2
            merged, rm2 = merge_runs(self.l1)
            self.l1.clear()
            self.l2.append(merged)
            self.stats["L1->L2"] += 1
            self.stats["dup_removed"] += rm2

    def _size(self, level):
        return sum(len(r) for r in (self.l1 if level == 1 else self.l2))

    # ---------- 写入：水位分层 ----------
    def insert(self, raw):
        p = Point(raw["ts"], raw["key"], raw["v"], raw["ver"], self.seq)
        self.seq += 1
        if self.max_ts is None:
            self.max_ts = p.ts
        self.max_ts = max(self.max_ts, p.ts)
        wm = self.max_ts - self.watermark
        if p.ts < wm:                            # 迟到超水位 -> 补偿通道
            self.stats["in_late"] += 1
            self.late_buf.append(p)
            if len(self.late_buf) > self.cap0:
                run, rm = self._seal(self.late_buf)
                self.late_buf.clear()
                self.late_runs.append(run)
                self.stats["late_seal"] += 1
                self.stats["late_dup_removed"] += rm
            return "late"
        self.stats["in_regular"] += 1            # 水位内 -> L0
        self.l0.append(p)
        if len(self.l0) > self.cap0:             # L0 满 -> 窗口排序后压入 L1
            self._flush_l0()
        return "regular"

    # ---------- 终态定型（确定性大压实，跨写入顺序得到同一布局） ----------
    def finalize(self):
        if self.l0:
            self._flush_l0()
        if self.l1:
            merged, rm = merge_runs(self.l1)
            self.l1, self.l2 = [], self.l2 + [merged]
            self.stats["L1->L2"] += 1
            self.stats["dup_removed"] += rm
        if len(self.l2) > 1:
            merged, rm = merge_runs(self.l2)
            self.l2 = [merged]
            self.stats["L2_major"] += 1
            self.stats["dup_removed"] += rm
        if self.late_buf:
            run, rm = self._seal(self.late_buf)
            self.late_buf.clear()
            self.late_runs.append(run)
            self.stats["late_seal"] += 1
            self.stats["late_dup_removed"] += rm
        if len(self.late_runs) > 1:
            merged, rm = merge_runs(self.late_runs)
            self.late_runs = [merged]
            self.stats["late_major"] += 1
            self.stats["late_dup_removed"] += rm

    # ---------- 跨层查询 ----------
    def _sources(self, include_late):
        src = [sorted(self.l0, key=_sort_key)] + self.l1 + self.l2
        if include_late:
            src += [sorted(self.late_buf, key=_sort_key)] + self.late_runs
        return src

    def scan(self, at, key=None, include_late=False):
        """点查询：ts in [0, at] 且 key 匹配，按 ts 升、同 ts ver 降（再 key 升）。"""
        merged, _ = merge_runs(self._sources(include_late))
        res = [p.as_dict() for p in merged
               if 0 <= p.ts <= at and (key is None or p.key == key)]
        res.sort(key=lambda d: (d["ts"], d["key"], -d["ver"]))
        return res

    def get(self, key, ts):
        """单点查询（含补偿通道），返回当前获胜版本或 None。"""
        merged, _ = merge_runs(self._sources(include_late=True))
        for p in merged:
            if p.key == key and p.ts == ts:
                return p.as_dict()
        return None

    # ---------- 规范化视图与摘要 ----------
    def normalized(self):
        regular, _ = merge_runs(self._sources(include_late=False))
        late, _ = merge_runs([sorted(self.late_buf, key=_sort_key)] + self.late_runs)
        return {"regular": [p.as_dict() for p in regular],
                "late": [p.as_dict() for p in late]}

    def digest(self):
        blob = json.dumps(self.normalized(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def snapshot(self):
        s = self.stats
        return {
            "L0": len(self.l0),
            "L1": self._size(1),
            "L2": self._size(2),
            "late": len(self.late_buf) + sum(len(r) for r in self.late_runs),
            "compactions": s["L0->L1"] + s["L1->L2"] + s["L2_major"],
            "runs": {"L0": 1 if self.l0 else 0, "L1": len(self.l1),
                     "L2": len(self.l2), "late": len(self.late_runs)},
        }


def run(req):
    eng = Engine(req["watermark"], req["levels"])
    for p in req["points"]:
        eng.insert(p)
    results = [{"at": q["at"], "key": q.get("key"),
                "points": eng.scan(q["at"], q.get("key"))}
               for q in req["queries"]]
    pre_snapshot, digest = eng.snapshot(), eng.digest()
    eng.finalize()
    return {
        "level_stats": {k: pre_snapshot[k] for k in ("L0", "L1", "L2", "late", "compactions")},
        "results": results,
        "digest": digest,                       # 内容摘要，与层布局/写入顺序无关
        "finalized": eng.snapshot(),
        "digest_final": eng.digest(),
        "compaction_detail": eng.stats,
        "late_points": eng.normalized()["late"],
    }


def main():
    req = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(run(req), f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
