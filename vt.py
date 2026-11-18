#!/usr/bin/env python3
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
# 迟滞请求保留的最大帧龄(age 超过该值即丢弃, 避免陈旧请求长期占队)
MAX_PENDING_AGE = 2


def parent_pid(pid, mip, grid):
    """四叉树父页: mip 级瓦片 (gx,gy) 的父为 mip-1 级 (gx//2, gy//2)。"""
    gx, gy = pid % grid, pid // grid
    return (gy // 2) * grid + (gx // 2)


def ancestor_chain(pid, mip, grid):
    """由近及远返回 (pid, mip) 祖先链(不含自身)。"""
    chain = []
    while mip > 0:
        pid, mip = parent_pid(pid, mip, grid), mip - 1
        chain.append((pid, mip))
    return chain


def _reduce_chunk(chunk):
    """工作线程: 对一个分块内的请求做 (pid,mip) 去重, 面积取最大值。"""
    merged = {}
    for r in chunk:
        key = (int(r["pid"]), int(r["mip"]))
        area = float(r.get("area", 0.0))
        old = merged.get(key)
        if old is None or area > old:
            merged[key] = area
    return merged


def aggregate_requests(raw, chunks=1, threads=1):
    """聚合一帧的原始请求 -> {(pid,mip): max(area)}。

    无论怎么分块、用几个线程、原始记录顺序如何, 输出完全一致(确定性合并)。
    """
    if not raw:
        return {}
    chunks = max(1, int(chunks))
    threads = max(1, int(threads))
    if chunks <= 1 or len(raw) <= 1:
        partials = [_reduce_chunk(raw)]
    else:
        size = (len(raw) + chunks - 1) // chunks
        blocks = [raw[i:i + size] for i in range(0, len(raw), size)]
        if threads <= 1:
            partials = [_reduce_chunk(b) for b in blocks]
        else:
            with ThreadPoolExecutor(max_workers=min(threads, len(blocks))) as ex:
                partials = list(ex.map(_reduce_chunk, blocks))
    merged = {}
    for part in partials:                       # 分块结果按 key 归一, 与顺序无关
        for key, area in part.items():
            old = merged.get(key)
            if old is None or area > old:
                merged[key] = area
    return merged


def _isqrt(n):
    r = int(n ** 0.5)
    while (r + 1) * (r + 1) <= n:
        r += 1
    while r * r > n:
        r -= 1
    return r


class PagingEngine:
    """带 Mip 回退与两阶段提交的驻留管理器。"""

    def __init__(self, pages, phys, bw, grid=None, fail_pid=None):
        self.pages = int(pages)                 # 每级 Mip 的虚拟页总数
        self.phys = int(phys)                   # 物理页槽位数
        self.bw = int(bw)                       # 每帧最大换页数(带宽)
        self.grid = int(grid) if grid else _isqrt(pages)
        # fail_pid: 故障注入, 该页"模拟 IO"永远失败, 用于验证两阶段回滚
        self.fail_pid = None if fail_pid is None else int(fail_pid)

        self.resident = {}                      # (pid,mip) -> slot
        self.slot_owner = {}                    # slot -> (pid,mip)
        self.last_used = {}                     # (pid,mip) -> 最后访问帧
        self.free = set(range(self.phys))
        self.pending = {}                       # (pid,mip) -> (area, age) 跨帧迟滞
        self.tick = 0

        self.stat_pageins = 0
        self.stat_faults = 0                    # 请求中未精确驻留的次数
        self.stat_fallbacks = 0                 # 经祖先回退满足的次数
        self.stat_hard = 0                      # 无任何驻留祖先的硬缺页次数
        self.stat_rollbacks = 0

    # ---- 缺页解析: 沿父链回退到最近驻留祖先 ----
    def resolve(self, pid, mip):
        if (pid, mip) in self.resident:
            return (pid, mip), True
        for anc in ancestor_chain(pid, mip, self.grid):
            if anc in self.resident:
                return anc, False
        return None, False

    def step(self, raw, chunks=1, threads=1):
        """处理一帧, 返回该帧的逐页诊断与计数。"""
        self.tick += 1
        tick = self.tick

        # 1) 本帧请求聚合(去重) + 跨帧迟滞队列老化
        current = aggregate_requests(raw, chunks, threads)
        aged = {}
        for key, (area, age) in self.pending.items():
            if age < MAX_PENDING_AGE:
                aged[key] = (area, age + 1)
        self.pending = aged

        requested = dict(aged)
        for key, area in current.items():
            old = requested.get(key)
            if old is None or area > old[0]:
                requested[key] = (area, 0)

        # 2) 缺页统计与回退解析(在任何换页之前, 反映本帧起始状态)
        faults = fallbacks = hard = 0
        protected = set()
        for (pid, mip) in requested:
            self.last_used[(pid, mip)] = tick
            hit, exact = self.resolve(pid, mip)
            if exact:
                continue
            faults += 1
            if hit is None:
                hard += 1
            else:
                fallbacks += 1
            # 回退路径上的祖先映射在本帧受保护, 保证回退始终有效
            for anc in ancestor_chain(pid, mip, self.grid):
                if anc in self.resident:
                    protected.add(anc)
                    break

        # 3) 优先级队列: mip 升序(粗粒度优先, 保证回退底图), 面积降序, pid 升序
        queue = sorted(
            (mip, -area, pid, key)
            for key, (area, _age) in requested.items()
            for (pid, mip) in [key]
        )

        # 4) 带宽受限的换页, 每次换页都是一个两阶段事务
        pageins = rollbacks = 0
        for _mip, _neg_area, _pid, key in queue:
            if pageins >= self.bw:
                break
            if key in self.resident:
                continue
            victim = self._pick_victim(protected, requested)
            txn = self._prepare(key, victim)          # 阶段一: 暂存(选槽+模拟IO)
            if txn is None:                           # 没有可回收槽且无空槽
                continue
            if txn["io_ok"]:
                self._commit(txn)                     # 阶段二: 先装新映射再回收
                pageins += 1
            else:
                self._restore(txn)                    # 失败回滚, 旧映射原样有效
                rollbacks += 1

        # 5) 重建迟滞队列: 仍未驻留的请求(本帧新请求年龄 0, 老请求递增)
        self.pending = {}
        for key, (area, age) in requested.items():
            if key not in self.resident:
                self.pending[key] = (area, age)

        self.stat_pageins += pageins
        self.stat_faults += faults
        self.stat_fallbacks += fallbacks
        self.stat_hard += hard
        self.stat_rollbacks += rollbacks
        return {
            "frame": tick - 1,
            "faults": faults,
            "fallbacks": fallbacks,
            "hard_faults": hard,
            "pageins": pageins,
            "rollbacks": rollbacks,
        }

    def _pick_victim(self, protected, requested):
        """LRU 选牺牲页: 跳过本帧请求页与回退祖先保护页; 同级按最久未用,
        细粒度页优先回收(保留粗粒度回退底图), 最后 pid 升序保证确定性。"""
        candidates = [
            key for key in self.resident
            if key not in protected and key not in requested
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda k: (self.last_used.get(k, 0), -k[1], k[0]),
        )

    def _prepare(self, key, victim):
        """阶段一(prepare): 选取物理槽并模拟 IO, 不动活动页表。"""
        if victim is not None:
            slot = self.resident[victim]
        elif self.free:
            slot = min(self.free)                    # 确定性取最小空槽
        else:
            return None
        io_ok = not (self.fail_pid is not None and key[0] == self.fail_pid)
        return {"key": key, "slot": slot, "victim": victim, "io_ok": io_ok}

    def _commit(self, txn):
        """阶段二(commit): 新映射先安装, 旧映射后回收, 期间回退始终可用。"""
        key, slot, victim = txn["key"], txn["slot"], txn["victim"]
        if victim is not None:
            del self.resident[victim]                # 摘除旧映射
            self.slot_owner.pop(slot, None)
        else:
            self.free.discard(slot)
        self.resident[key] = slot                    # 安装新映射(先于回收完成)
        self.slot_owner[slot] = key
        self.last_used[key] = self.tick

    def _restore(self, txn):
        """事务回滚: 暂存从未触及活动页表, 显式恢复后状态与提交前一致。"""
        victim, slot = txn["victim"], txn["slot"]
        if victim is not None:
            self.resident[victim] = slot
            self.slot_owner[slot] = victim
        else:
            self.free.add(slot)

    # ---- 输出 ----
    def resident_pids(self):
        return sorted(pid for (pid, _mip) in self.resident)

    def resident_entries(self):
        return sorted((pid, mip, self.resident[(pid, mip)])
                      for (pid, mip) in self.resident)

    def report(self):
        return {
            "resident": [],                          # 由 run() 逐帧填充
            "resident_detail": [],
            "faults": [],
            "fallbacks": [],
            "hard_faults": [],
            "pageins": [],
            "rollbacks": [],
            "pending": [],
            "digest": "",
        }


def run(req, chunks=1, threads=1, fail_pid=None):
    """回放整个请求序列, 生成确定性报告。"""
    eng = PagingEngine(req["pages"], req["phys"], req["bw"],
                       grid=req.get("grid"), fail_pid=fail_pid)
    report = eng.report()
    for raw in req["frames"]:
        diag = eng.step(raw, chunks=chunks, threads=threads)
        report["resident"].append(eng.resident_pids())
        report["resident_detail"].append(eng.resident_entries())
        report["faults"].append(diag["faults"])
        report["fallbacks"].append(diag["fallbacks"])
        report["hard_faults"].append(diag["hard_faults"])
        report["pageins"].append(diag["pageins"])
        report["rollbacks"].append(diag["rollbacks"])
        report["pending"].append(sorted(eng.pending))
    canonical = json.dumps(
        {
            "pages": eng.pages,
            "phys": eng.phys,
            "grid": eng.grid,
            "resident_detail": report["resident_detail"],
            "pageins": report["pageins"],
            "faults": report["faults"],
            "fallbacks": report["fallbacks"],
            "hard_faults": report["hard_faults"],
            "rollbacks": report["rollbacks"],
        },
        sort_keys=True, separators=(",", ":"),
    )
    report["digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    return report
def main(argv):
    if len(argv) < 3:
        print("usage: python3 vt.py req.json output.json [chunks] [threads]",
              file=sys.stderr)
        return 2
    chunks = int(argv[3]) if len(argv) > 3 else 1
    threads = int(argv[4]) if len(argv) > 4 else 1
    with open(argv[1]) as f:
        req = json.load(f)
    report = run(req, chunks=chunks, threads=threads)
    with open(argv[2], "w") as f:
        json.dump(report, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
