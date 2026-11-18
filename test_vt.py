#!/usr/bin/env python3
"""vt.py 验收测试:
  1. 手工参考策略逐帧核对驻留集合/计数(含 Mip 回退、带宽限制、跨帧迟滞)。
  2. 分块/线程/请求重放顺序不改变最终驻留集合与页表摘要。
  3. 高速回放 + 大带宽限制下硬缺页帧比例 <= 5%。
  4. 两阶段提交故障注入: IO 失败时旧映射保持有效并计入回滚。
"""
import json
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vt  # noqa: E402

GRID = 4
PAGES = 16
PHYS = 4
BW = 2


def r(pid, mip, area=1.0):
    return {"pid": pid, "mip": mip, "area": area}


def test_handcrafted_reference():
    """手工推导的参考策略: 队列顺序 (mip asc, area desc, pid asc) + LRU 回收。"""
    frames = [
        # 帧0: 同帧重复请求必须合并; mip0 粗页优先, mip2 面积大者次之
        [r(0, 0, 1), r(0, 0, 1), r(4, 2, 0.9), r(5, 2, 0.5), r(1, 0, 1)],
        # 帧1: (4,2) 已驻留; 新页 (8,2), 触发 LRU 回收
        [r(4, 2, 0.9), r(8, 2, 0.9)],
        # 帧2: 仅新页 (12,1); (8,2) 不再被请求, 成为最久未用牺牲页
        [r(12, 1, 0.9)],
    ]
    rep = vt.run({"pages": PAGES, "phys": PHYS, "bw": BW,
                  "grid": GRID, "frames": frames})

    detail = rep["resident_detail"]
    # 帧0: 带宽 2 -> mip0 的 (0,0)/(1,0) 优先驻留; (4,2)/(5,2) 进入迟滞队列
    assert [(p, m) for p, m, _ in detail[0]] == [(0, 0), (1, 0)], detail[0]
    # 帧1: 空闲槽装入 (4,2); LRU 并列按 pid 回收 (0,0), 装入 (8,2);
    # 三个细页的父链都能回退到驻留的 (0,0)/(1,0), 无硬缺页
    assert [(p, m) for p, m, _ in detail[1]] == [(0, 0), (4, 2), (8, 2)], detail[1]
    # 帧2: 迟滞的 (5,2) 是 mip2 排在 mip1 的 (12,1) 之前, 先占槽(回收 (8,2));
    # 随后 (12,1) 回收 LRU 最久的 (4,2)
    assert [(p, m) for p, m, _ in detail[2]] == [(0, 0), (5, 2), (12, 1)], detail[2]

    # 逐帧计数(缺页统计包含本帧仍未满足的迟滞请求)
    assert rep["pageins"] == [2, 2, 2], rep["pageins"]
    assert rep["faults"] == [4, 3, 2], rep["faults"]
    # 帧1 三个细页都能经父链回退到驻留的 (0,0)/(1,0); 帧2 (12,1) 祖先 (4,0)
    # 未驻留 -> 唯一硬缺页, (5,2) 仍回退到 (0,0)
    assert rep["hard_faults"] == [4, 0, 1], rep["hard_faults"]
    assert rep["fallbacks"] == [0, 3, 1], rep["fallbacks"]
    assert rep["rollbacks"] == [0, 0, 0], rep["rollbacks"]
    # 物理槽位不重复且不越界
    for frame in detail:
        slots = [s for _, _, s in frame]
        assert len(slots) == len(set(slots))
        assert all(0 <= s < PHYS for s in slots)
    print("ok 1 handcrafted reference (resident + counters)")


def test_mip_fallback():
    """缺页时沿父链回退到最近驻留祖先, 祖先映射在本帧受保护不被回收。"""
    # grid=4: pid4=(gx0,gy1) -> 父 pid0=(0,0)@mip1 -> 父 pid0=(0,0)@mip0
    assert vt.parent_pid(4, 2, GRID) == 0
    assert vt.parent_pid(2, 1, GRID) == 1
    assert vt.ancestor_chain(4, 2, GRID) == [(0, 1), (0, 0)]
    frames = [
        [r(0, 0, 1.0)],                                   # 帧0: 装入底图 (0,0)
        [r(4, 2, 0.9), r(5, 2, 0.9), r(6, 2, 0.9), r(7, 2, 0.9)],
        # 帧1: 4 个细页都缺页, 但全部回退到 (0,0); bw=2 只能装 2 个
    ]
    rep = vt.run({"pages": PAGES, "phys": PHYS, "bw": BW,
                  "grid": GRID, "frames": frames})
    assert rep["faults"][1] == 4
    assert rep["fallbacks"][1] == 4, rep["fallbacks"]
    assert rep["hard_faults"][1] == 0
    assert rep["pageins"][1] == 2
    # 回退祖先 (0,0) 必须仍在驻留集
    assert (0, 0) in [(p, m) for p, m, _ in rep["resident_detail"][1]]

    # 无任何祖先驻留时记为硬缺页(pid8 父链为 (4,1)->(2,0), 均未驻留)
    rep2 = vt.run({"pages": PAGES, "phys": PHYS, "bw": BW, "grid": GRID,
                   "frames": [[r(8, 2, 0.9)]]})
    assert rep2["hard_faults"][0] == 1 and rep2["fallbacks"][0] == 0
    print("ok 2 mip fallback + ancestor protection + hard fault")


def test_determinism():
    """分块大小、线程数、请求重放顺序均不改变驻留集合与摘要。"""
    rng = random.Random(42)
    frames = []
    for _ in range(12):
        n = rng.randint(1, 18)
        frames.append([r(rng.randrange(PAGES), rng.randrange(3),
                         round(rng.random(), 3)) for _ in range(n)])
    base = {"pages": PAGES, "phys": PHYS, "bw": BW, "grid": GRID}

    baseline = vt.run(dict(base, frames=frames), chunks=1, threads=1)
    configs = [(1, 1), (2, 1), (3, 2), (5, 4), (8, 8), (13, 3)]
    digests = {baseline["digest"]}
    for chunks, threads in configs:
        rep = vt.run(dict(base, frames=frames), chunks=chunks, threads=threads)
        assert rep["resident"] == baseline["resident"]
        assert rep["resident_detail"] == baseline["resident_detail"]
        assert rep["faults"] == baseline["faults"]
        assert rep["pageins"] == baseline["pageins"]
        digests.add(rep["digest"])

    # 每帧内部请求随机重排(含重复项), 多次实验摘要一致
    for seed in range(8):
        shuffled = []
        r2 = random.Random(100 + seed)
        for frame in frames:
            f = list(frame)
            r2.shuffle(f)
            # 额外注入重复请求, 验证同帧聚合
            f += [dict(f[r2.randrange(len(f))]) for _ in range(3)]
            r2.shuffle(f)
            shuffled.append(f)
        rep = vt.run(dict(base, frames=shuffled), chunks=4, threads=4)
        assert rep["digest"] == baseline["digest"], seed
        assert rep["resident"] == baseline["resident"]
        digests.add(rep["digest"])
    assert len(digests) == 1
    print("ok 3 determinism across chunks/threads/replay-order, digest=%s"
          % baseline["digest"][:12])


def test_high_speed_budget():
    """高速回放 + 大带宽限制: 硬缺页帧数不超过总帧数的 5%。

    场景: 相机沿网格平移, 每帧视野内 mip0 底图 + 若干 mip2 细节页;
    底图覆盖整屏保证回退有效, 细节页按需流式载入。
    """
    rng = random.Random(7)
    n_frames = 120
    frames = []
    for t in range(n_frames):
        cam = t % GRID
        reqs = [r(g, 0, 1.0) for g in range(GRID)]      # 全量粗 mip 底图
        for _ in range(6):                              # 视野内 6 个细节页
            gx = (cam + rng.randrange(2)) % GRID
            gy = rng.randrange(GRID)
            reqs.append(r(gy * GRID + gx, 2, round(rng.random(), 3)))
        frames.append(reqs)
    rep = vt.run({"pages": PAGES, "phys": 8, "bw": 3,
                  "grid": GRID, "frames": frames})
    # 带宽约束: 每帧换页数不超过 bw
    assert all(p <= 3 for p in rep["pageins"])
    hard_frames = sum(1 for h in rep["hard_faults"] if h > 0)
    ratio = hard_frames / n_frames
    assert ratio <= 0.05, "hard-fault frames %.1f%% > 5%%" % (100 * ratio)
    total_faults = sum(rep["faults"])
    total_fallback = sum(rep["fallbacks"])
    print("ok 4 high-speed replay: hard-fault frames %d/%d = %.2f%%, "
          "fallbacks %d/%d faults"
          % (hard_frames, n_frames, 100 * ratio, total_fallback, total_faults))


def test_two_phase_rollback():
    """故障注入: 目标页 IO 永远失败 -> 事务回滚, 旧物理映射保持有效。"""
    frames = [
        [r(0, 0, 1), r(1, 0, 1)],                       # 帧0 正常装入 2 页
        [r(9, 1, 1)],                                   # 帧1 目标页 IO 失败
    ]
    rep = vt.run({"pages": PAGES, "phys": PHYS, "bw": BW, "grid": GRID,
                  "frames": frames}, fail_pid=9)
    assert rep["rollbacks"][1] == 1
    assert rep["pageins"][1] == 0
    # 回滚后帧1驻留集与帧0完全一致, 槽位映射不变
    assert rep["resident_detail"][1] == rep["resident_detail"][0]
    assert [(p, m) for p, m, _ in rep["resident_detail"][1]] == [(0, 0), (1, 0)]
    # 失败页进入迟滞队列, 下帧仍会重试(但仍失败)
    rep_b = vt.run({"pages": PAGES, "phys": PHYS, "bw": BW, "grid": GRID,
                    "frames": [frames[0], [r(9, 1, 1)], [r(9, 1, 1)]]},
                   fail_pid=9)
    assert rep_b["rollbacks"] == [0, 1, 1]
    assert rep_b["resident"][2] == rep_b["resident"][0]
    print("ok 5 two-phase commit rollback keeps stale mapping valid")


def test_hysteresis_aging():
    """跨帧迟滞: 带宽不足未满足的请求保留到后续帧, 超过帧龄上限后丢弃。"""
    frames = [
        [r(0, 0, 1), r(1, 0, 1), r(2, 0, 1)],           # bw=2, (2,0) 排队
        [r(3, 0, 1)],                                    # (2,0) 与 (3,0) 竞争
    ]
    rep = vt.run({"pages": PAGES, "phys": PHYS, "bw": BW,
                  "grid": GRID, "frames": frames})
    # 帧0 后 (2,0) 在迟滞队列
    assert (2, 0) in [tuple(k) for k in rep["pending"][0]]
    # 帧1: 队列中 (2,0) age=1 与新请求 (3,0) age=0, mip/pid 决定 (2,0) 先装入
    assert rep["pageins"] == [2, 2]
    assert (2, 0) in [(p, m) for p, m, _ in rep["resident_detail"][1]]

    # 超过 MAX_PENDING_AGE 的陈旧请求被丢弃, 不永久占队
    long_idle = [[r(i, 0, 1)] for i in range(6)]
    rep2 = vt.run({"pages": PAGES, "phys": 2, "bw": 1,
                   "grid": GRID, "frames": long_idle})
    assert all(len(p) <= 4 for p in rep2["pending"])
    print("ok 6 cross-frame hysteresis and aging")


def test_cli_end_to_end():
    """命令行端到端: 输出文件结构与库内调用一致。"""
    req = {"pages": PAGES, "phys": PHYS, "bw": BW, "grid": GRID,
           "frames": [[r(0, 0, 1), r(0, 0, 1), r(4, 2, 0.9)],
                      [r(4, 2, 0.9), r(8, 2, 0.9)]]}
    here = os.path.dirname(os.path.abspath(__file__))
    req_path = os.path.join(here, "_req_tmp.json")
    out_path = os.path.join(here, "_out_tmp.json")
    with open(req_path, "w") as f:
        json.dump(req, f)
    subprocess.check_call(
        [sys.executable, os.path.join(here, "vt.py"),
         req_path, out_path, "3", "2"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(out_path) as f:
        cli = json.load(f)
    lib = vt.run(req, chunks=3, threads=2)
    assert cli["resident"] == lib["resident"]
    assert cli["digest"] == lib["digest"]
    assert len(cli["resident"]) == 2 and len(cli["faults"]) == 2
    os.remove(req_path)
    os.remove(out_path)
    print("ok 7 cli end-to-end matches library output")


if __name__ == "__main__":
    test_handcrafted_reference()
    test_mip_fallback()
    test_determinism()
    test_high_speed_budget()
    test_two_phase_rollback()
    test_hysteresis_aging()
    test_cli_end_to_end()
    print("ALL TESTS PASSED")
